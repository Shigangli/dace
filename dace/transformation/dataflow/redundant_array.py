# Copyright 2019-2020 ETH Zurich and the DaCe authors. All rights reserved.
""" Contains classes that implement a redundant array removal transformation.
"""

import functools
from copy import deepcopy as dcpy

from dace import registry, subsets
from dace.sdfg import nodes
from dace.sdfg import utils as sdutil
from dace.transformation import transformation as pm
from dace.config import Config


@registry.autoregister_params(singlestate=True, strict=True)
class RedundantArray(pm.Transformation):
    """ Implements the redundant array removal transformation, applied
        when a transient array is copied to and from (to another array),
        but never used anywhere else. """

    _arrays_removed = 0
    _in_array = nodes.AccessNode("_")
    _out_array = nodes.AccessNode("_")

    @staticmethod
    def expressions():
        return [
            sdutil.node_path_graph(RedundantArray._in_array,
                                   RedundantArray._out_array)
        ]

    @staticmethod
    def can_be_applied(graph, candidate, expr_index, sdfg, strict=False):
        in_array = graph.nodes()[candidate[RedundantArray._in_array]]
        out_array = graph.nodes()[candidate[RedundantArray._out_array]]

        in_desc = in_array.desc(sdfg)
        out_desc = out_array.desc(sdfg)

        # Ensure out degree is one (only one target, which is out_array)
        if graph.out_degree(in_array) != 1:
            return False

        # Make sure that the candidate is a transient variable
        if not in_desc.transient:
            return False

        # Make sure that both arrays are using the same storage location
        # and are of the same type (e.g., Stream->Stream)
        if in_desc.storage != out_desc.storage:
            return False
        if type(in_desc) != type(out_desc):
            return False

        # Find occurrences in this and other states
        occurrences = []
        for state in sdfg.nodes():
            occurrences.extend([
                n for n in state.nodes()
                if isinstance(n, nodes.AccessNode) and n.desc(sdfg) == in_desc
            ])

        if len(occurrences) > 1:
            return False

        # Only apply if arrays are of same shape (no need to modify subset)
        if len(in_desc.shape) != len(out_desc.shape) or any(
                i != o for i, o in zip(in_desc.shape, out_desc.shape)):
            return False

        if strict:
            # In strict mode, make sure the memlet covers the removed array
            edge = graph.edges_between(in_array, out_array)[0]
            if any(m != a
                   for m, a in zip(edge.data.subset.size(), in_desc.shape)):
                return False

        return True

    @staticmethod
    def match_to_str(graph, candidate):
        in_array = graph.nodes()[candidate[RedundantArray._in_array]]

        return "Remove " + str(in_array)

    def apply(self, sdfg):
        def gnode(nname):
            return graph.nodes()[self.subgraph[nname]]

        graph = sdfg.nodes()[self.state_id]
        in_array = gnode(RedundantArray._in_array)
        out_array = gnode(RedundantArray._out_array)

        for e in graph.in_edges(in_array):
            # Modify all incoming edges to point to out_array
            path = graph.memlet_path(e)
            for pe in path:
                if pe.data.data == in_array.data:
                    pe.data.data = out_array.data

            # Redirect edge to out_array
            graph.remove_edge(e)
            graph.add_edge(e.src, e.src_conn, out_array, e.dst_conn, e.data)

        # Finally, remove in_array node
        graph.remove_node(in_array)
        # TODO: Should the array be removed from the SDFG?
        # del sdfg.arrays[in_array]
        if Config.get_bool("debugprint"):
            RedundantArray._arrays_removed += 1


@registry.autoregister_params(singlestate=True, strict=True)
class RedundantSecondArray(pm.Transformation):
    """ Implements the redundant array removal transformation, applied
        when a transient array is copied from and to (from another array),
        but never used anywhere else. This transformation removes the second
        array. """

    _arrays_removed = 0
    _in_array = nodes.AccessNode("_")
    _out_array = nodes.AccessNode("_")

    @staticmethod
    def expressions():
        return [
            sdutil.node_path_graph(RedundantSecondArray._in_array,
                                   RedundantSecondArray._out_array)
        ]

    @staticmethod
    def can_be_applied(graph, candidate, expr_index, sdfg, strict=False):
        in_array = graph.nodes()[candidate[RedundantSecondArray._in_array]]
        out_array = graph.nodes()[candidate[RedundantSecondArray._out_array]]

        in_desc = in_array.desc(sdfg)
        out_desc = out_array.desc(sdfg)

        # Ensure in degree is one (only one source, which is in_array)
        if graph.in_degree(out_array) != 1:
            return False

        # Make sure that the candidate is a transient variable
        if not out_desc.transient:
            return False

        # Make sure that both arrays are using the same storage location
        # and are of the same type (e.g., Stream->Stream)
        if in_desc.storage != out_desc.storage:
            return False
        if type(in_desc) != type(out_desc):
            return False

        # Find occurrences in this and other states
        occurrences = []
        for state in sdfg.nodes():
            occurrences.extend([
                n for n in state.nodes()
                if isinstance(n, nodes.AccessNode) and n.desc(sdfg) == out_desc
            ])

        if len(occurrences) > 1:
            return False

        # Check whether the data copied from the first datanode cover
        # the subsets of all the output edges of the second datanode.
        # 1. Extract the input (first) and output (second) array subsets.
        memlet = graph.edges_between(in_array, out_array)[0].data
        if memlet.data == in_array.data:
            inp_subset = memlet.subset
            out_subset = memlet.other_subset
        else:
            inp_subset = memlet.other_subset
            out_subset = memlet.subset
        
        if not inp_subset:
            inp_subset = dcpy(out_subset)
            inp_subset.offset(out_subset, negative=True)
        if not out_subset:
            out_subset = dcpy(inp_subset)
            out_subset.offset(inp_subset, negative=True)

        def _prod(sequence):
            return functools.reduce(lambda a, b: a * b, sequence, 1)

        # 2. If the data copied from the first array are equal in size
        # to the second array, then all subsets are covered.
        if (inp_subset.num_elements() == _prod(out_desc.shape)):
            return True

        # 3. Check each output edge of the second array
        for e in graph.out_edges(out_array):
            # 3a. Extract the output edge subset
            if e.data.data == out_array.data:
                subset = e.data.subset
            else:
                if e.data.other_subset:
                    subset = e.data.other_subset
                else:
                    subset = dcpy(e.data.other_subset)
                    subset.offset(e.data.other_subset, negative=True)
            # 3b. Check subset coverage
            if not out_subset.covers(subset):
                return False

        return True

    @staticmethod
    def match_to_str(graph, candidate):
        out_array = graph.nodes()[candidate[RedundantSecondArray._out_array]]

        return "Remove " + str(out_array)

    def apply(self, sdfg):
        def gnode(nname):
            return graph.nodes()[self.subgraph[nname]]

        graph = sdfg.nodes()[self.state_id]
        in_array = gnode(RedundantSecondArray._in_array)
        out_array = gnode(RedundantSecondArray._out_array)

        # Extract the input (first) and output (second) array subsets.
        memlet = graph.edges_between(in_array, out_array)[0].data
        if memlet.data == in_array.data:
            inp_subset = memlet.subset
            out_subset = memlet.other_subset
        else:
            inp_subset = memlet.other_subset
            out_subset = memlet.subset
        
        if not inp_subset:
            inp_subset = dcpy(out_subset)
            inp_subset.offset(out_subset, negative=True)
        if not out_subset:
            out_subset = dcpy(inp_subset)
            out_subset.offset(inp_subset, negative=True)

        for e in graph.out_edges(out_array):
            # Modify all outgoing edges to point to in_array
            path = graph.memlet_tree(e)
            for pe in path:
                if pe.data.data == out_array.data:
                    pe.data.data = in_array.data
                    # Here we assume that the input subset covers the output
                    # subset, since this was already checked in can_be_applied.
                    # Example
                    # inp -- (0, a:b)/(c:c+b) --> out -- (c+d) --> other
                    # must become
                    # inp -- (0, a+d) --> other
                    subset = pe.data.subset
                    # (c+d) - (c:c+b) = (d)
                    subset.offset(out_subset, negative=True)
                    # (0, a:b)(d) = (0, a+d) (or offset for indices)
                    if isinstance(inp_subset, subsets.Indices):
                        tmp = dcpy(inp_subset)
                        tmp.offset(subset, negative=False)
                        subset = tmp
                    else:
                        subset = inp_subset.compose(subset)
                    pe.data.subset = subset
                    # if isinstance(subset, subsets.Indices):
                    #     pe.data.subset.offset(subset, False)
                    # else:
                    #     pe.data.subset = subset.compose(pe.data.subset)
                elif pe.data.other_subset:
                    # We do the same, but for other_subset
                    # We do not change the data
                    subset = pe.data.other_subset
                    subset.offset(out_subset, negative=True)
                    if isinstance(inp_subset, subsets.Indices):
                        tmp = dcpy(inp_subset)
                        tmp.offset(subset, negative=False)
                        subset = tmp
                    else:
                        subset = inp_subset.compose(subset)
                    pe.data.other_subset = subset
                else:
                    # The subset is the entirety of the out array
                    # Assuming that the input subset covers this,
                    # we do not need to do anything
                    pass

            # Redirect edge to out_array
            graph.remove_edge(e)
            graph.add_edge(in_array, e.src_conn, e.dst, e.dst_conn, e.data)

        # Finally, remove out_array node
        graph.remove_node(out_array)
        # TODO: Should the array be removed from the SDFG?
        # del sdfg.arrays[out_array]
        if Config.get_bool("debugprint"):
            RedundantSecondArray._arrays_removed += 1
