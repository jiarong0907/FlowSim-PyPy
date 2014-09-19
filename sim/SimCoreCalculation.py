#!/usr/bin/python
# -*- coding: utf-8 -*-
"""sim/SimCore.py: Class SimCoreCalculation, containing flow rate recalculation functions.
Inherited by SimCore.
"""
__author__      = 'Kuan-yin Chen'
__copyright__   = 'Copyright 2014, NYU-Poly'

# Built-in modules
import os
import csv
from heapq import heappush, heappop
from math import ceil, log
from time import time
# Third-party modules
import networkx as nx
import netaddr as na
import pandas as pd
import numpy as np
# User-defined modules
from SimConfig import *
from SimCtrl import *
from SimFlowGen import *
from SimFlow import *
from SimSwitch import *
from SimLink import *
from SimEvent import *

class SimCoreCalculation:
    """Flow rate calculation-related codes for SimCore class.
    """

    def calc_flow_rates_src_limited(self, ev_time):
        """Calculate flow rates (according to DevoFlow Algorithm 1), but is aware of
        each flow's source rate constraints.

        Args:
            None

        Returns:
            None. But will update flow and link statistics, as well as identify next ending
            flow and its estimated end time.

        """
        heap_flows = []             # Keep a minheap for all flows, indexed by their
                                    # flowobj.flow_rate (max source rate of flow)
        btnk_link = ('', '')        # Bottleneck link: defined by -
                                    #   argmin_{all links}
                                    #   {linkobj.unasgn_bw / linkobj.n_unasgn_flows}
        btnk_bw = float('inf')      # As shown above, bottleneck BW defined by -
                                    #   linkobj.unasgn_bw / linkobj.n_unasgn_flows
        n_unprocessed_links = len(self.links)
        earliest_end_time   = float('inf')
        earliest_end_flow   = ('', '')

        # Initialize flow variables and push to min-heap indexed by flow_rate
        for fl in self.flows:
            flowobj = self.flows[fl]
            if (not flowobj.status == 'active'):
                continue
            else:
                flowobj.assigned = False
                heappush(heap_flows, (flowobj.flow_rate, flowobj, fl))

        # Initialize link variables and find first-round bottleneck link
        for lk in self.links:
            linkobj = self.linkobjs[lk]
            linkobj.unasgn_bw       = linkobj.cap
            linkobj.n_active_flows  = linkobj.get_n_active_flows()
            linkobj.n_unasgn_flows  = linkobj.n_active_flows
            linkobj.processed       = False
            if (linkobj.n_unasgn_flows > 0):
                linkobj.bw_per_flow     = linkobj.unasgn_bw / float(linkobj.n_unasgn_flows)
                if (linkobj.bw_per_flow < btnk_bw):
                    btnk_link   = lk
                    btnk_bw     = linkobj.bw_per_flow
            else:
                n_unprocessed_links -= 1

        while True:
            if (n_unprocessed_links == 0 or len(heap_flows) == 0):
                break

            if (heap_flows[0][1].assigned == True):
                heappop(heap_flows)
                continue

            # Get the flow BW, flowobj and flow key at root of minheap
            mice_bw, mice_flowobj, mice_flowkey = heap_flows[0]

            recalc_btnk = False     # Flag for recalculation of global bottlneck

            # Case 1: BW assigned to flow(s) limited by the mice flow's own flow_rate
            if (mice_bw < btnk_bw):
                #print "process flow"
                heappop(heap_flows)

                # Update this flow
                est_end_time, bytes_sent_since_update = \
                        mice_flowobj.update_flow(ev_time, mice_bw)

                if (est_end_time < earliest_end_time):
                    earliest_end_time = est_end_time
                    earliest_end_flow = mice_flowkey

                # Update links traversed by the flow
                for lk in self.get_links_on_path(mice_flowobj.path):
                    self.link_byte_cnt[lk]  +=  bytes_sent_since_update
                    linkobj                 =   self.linkobjs[lk]
                    linkobj.unasgn_bw       -=  mice_bw
                    linkobj.n_unasgn_flows  -=  1
                    if (linkobj.n_unasgn_flows > 0):
                        linkobj.bw_per_flow     =   linkobj.unasgn_bw /             \
                                                    float(linkobj.n_unasgn_flows)
                        if (linkobj.bw_per_flow < btnk_bw):
                            btnk_link   = lk
                            btnk_bw     = linkobj.bw_per_flow
                    else:
                        if (lk == btnk_link):
                            recalc_btnk = True
                        n_unprocessed_links -= 1

            # Case 2: BW assigned to flows(s) limited by max-min fair on btnk_link
            else:
                #print "process link"
                # Process all links on the btnk_link
                for fl in self.linkobjs[btnk_link].flows:
                    flowobj = self.flows[fl]

                    if (not flowobj.status == 'active'):
                        continue

                    elif (flowobj.assigned == True):
                        continue

                    else:
                        # Calculate estimated end time of this flow
                        est_end_time, bytes_sent_since_update = \
                                flowobj.update_flow(ev_time, btnk_bw)
                        if (est_end_time < earliest_end_time):
                            earliest_end_time = est_end_time
                            earliest_end_flow = fl

                        # Update links traversed by this flow
                        for lk in self.get_links_on_path(flowobj.path):
                            self.link_byte_cnt[lk]  +=  bytes_sent_since_update
                            linkobj                 =   self.linkobjs[lk]
                            linkobj.unasgn_bw       -=  btnk_bw
                            linkobj.n_unasgn_flows  -=  1
                            if (linkobj.n_unasgn_flows > 0):
                                linkobj.bw_per_flow =   linkobj.unasgn_bw /             \
                                                        float(linkobj.n_unasgn_flows)
                                if (linkobj.bw_per_flow < btnk_bw):
                                    btnk_link   = lk
                                    btnk_bw     = linkobj.bw_per_flow
                            else:
                                if (lk == btnk_link):
                                    recalc_btnk = True
                                n_unprocessed_links -=  1

            #if (n_unprocessed_links == 0 or len(heap_flows) == 0):
            #    break

            if (n_unprocessed_links > 0 and recalc_btnk == True):
                #print "Recalculate btnk"
                btnk_link = min([lk for lk in self.links \
                                 if self.linkobjs[lk].n_unasgn_flows > 0], \
                                key=lambda x: self.linkobjs[x].bw_per_flow)
                btnk_bw = self.linkobjs[btnk_link].bw_per_flow
                #print btnk_link, btnk_bw

        # Finally, update the estimated earliest-ending flow
        self.next_end_time = earliest_end_time
        self.next_end_flow = earliest_end_flow


    def calc_flow_rates_src_unlimited(self, ev_time):
        """Calculate flow rates (according to DevoFlow Algorithm 1). Flows can transmit
        as fast as possible under link capacity constraints and max-min fairness.

        Args:
            None

        Returns:
            None. But will update flow and link statistics, as well as identify next ending
            flow and its estimated end time.

        """
        # The following local dicts have links as their keys (represented by 2-tuple of node names)
        # Values are either float64 (caps, unasgn_caps) or int (active_flows, unasgn_flows)
        link_bw = {}                # Value: maximum BW on that link
        link_unasgn_bw = {}         # Value: Unassigned BW on that link
        link_n_active_flows = {}    # Value: # of active flows on that link
        link_n_unasgn_flows = {}    # Value: # of unassigned flows on that link

        # The following local dicts have flows as their keys (represented by 2-tuple of IPs)
        flow_asgn = {}          # Value: boolean that signals whether the flow is assigned.

        # Initialization:
        for lk in self.links:
            cap = self.linkobjs[lk].cap
            n_active_flows = self.linkobjs[lk].get_n_active_flows()
            link_unasgn_bw[lk] = link_bw[lk] = cap
            link_n_unasgn_flows[lk] = link_n_active_flows[lk] = n_active_flows

        for fl in self.flows:
            if (not self.flows[fl].status == 'active'):
                continue
            else:
                flow_asgn[fl] = False

        # ---- Start iterating over bottleneck links ----
        list_unfin_links = [lk for lk in self.links if link_n_unasgn_flows[lk] > 0]
                                                        # List of links that are not yet processed
        earliest_end_time = 99999999.9  # Just a very large float number
        earliest_end_flow = ('', '')

        while (len(list_unfin_links) > 0):
            # Find the bottleneck link (link with minimum avg. BW for unassigned links)
            btneck_link = sorted(list_unfin_links, \
                                 key=lambda lk: link_unasgn_bw[lk]/link_n_unasgn_flows[lk], \
                                 reverse=False)[0]
            btneck_bw = link_unasgn_bw[btneck_link]/link_n_unasgn_flows[btneck_link]

            # Update all active flows on bottleneck links
            for fl in self.linkobjs[btneck_link].flows:
                flowobj = self.flows[fl]    # Store the pointer to self.flows[fl]!
                                            # This makes exec time a lot shorter!
                if (not flowobj.status == 'active'):
                    continue
                #if (fl in flow_asgn):
                else:
                    if (flow_asgn[fl] == False):
                        # ---- Flow operations ----
                        # Write btneck_bw to flow
                        flow_asgn[fl] = True

                        # Write updated statistics to flow: curr_rate, bytes_left, bytes_sent,
                        # update_time, etc.
                        bytes_sent_since_update = flowobj.curr_rate *                   \
                                                (ev_time - flowobj.update_time)
                        flowobj.bytes_left  -=  bytes_sent_since_update
                        flowobj.bytes_sent  =   flowobj.flow_size - flowobj.bytes_left
                        flowobj.update_time =   ev_time
                        flowobj.avg_rate    =   flowobj.bytes_sent /                    \
                                                (ev_time - flowobj.arrive_time)

                        # Calculate & update next ending flow and its estimated end time
                        flowobj.curr_rate   =   btneck_bw
                        est_end_time        =   ev_time + \
                                                (flowobj.bytes_left / flowobj.curr_rate)
                        if (est_end_time < earliest_end_time):
                            earliest_end_time = est_end_time
                            earliest_end_flow = fl

                        # ---- Link operations ----
                        # Update link unassigned BW and link unassigned flows along the path
                        path = flowobj.path

                        for lk in self.get_links_on_path(path):
                            self.link_byte_cnt[lk]  +=  bytes_sent_since_update
                            link_unasgn_bw[lk]      -=  btneck_bw
                            link_n_unasgn_flows[lk] -=  1
                            if (link_n_unasgn_flows[lk] == 0 or link_unasgn_bw[lk] == 0.0):
                                list_unfin_links.remove(lk)

        self.next_end_time = earliest_end_time
        self.next_end_flow = earliest_end_flow


    def calc_flow_rates(self, ev_time):
        """
        """

        if (cfg.SRC_LIMITED > 0):
            self.calc_flow_rates_src_limited(ev_time)
        else:
            self.calc_flow_rates_src_unlimited(ev_time)
