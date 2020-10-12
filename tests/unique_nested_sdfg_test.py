# Copyright 2019-2020 ETH Zurich and the DaCe authors. All rights reserved.

# The scope of the test is to verify that code nested SDFGs with a unique name is generated only once
# The nested SDFG compute vector addition



import dace
import numpy as np
import argparse
import subprocess

from dace.memlet import Memlet

def make_vecAdd_sdfg(symbol_name: str, sdfg_name:str, access_nodes_dict : dict, dtype = dace.float32):
    n = dace.symbol(symbol_name)
    vecAdd_sdfg = dace.SDFG(sdfg_name)
    vecAdd_state = vecAdd_sdfg.add_state()


    # ---------- ----------
    # ACCESS NODES
    # ---------- ----------

    x_name = access_nodes_dict["x"]
    y_name = access_nodes_dict["y"]
    z_name = access_nodes_dict["z"]

    vecAdd_sdfg.add_array(x_name, [n], dtype=dtype)
    vecAdd_sdfg.add_array(y_name, [n], dtype=dtype)
    vecAdd_sdfg.add_array(z_name, [n], dtype=dtype)

    x_in = vecAdd_state.add_read(x_name)
    y_in = vecAdd_state.add_read(y_name)
    z_out = vecAdd_state.add_write(z_name)


    # ---------- ----------
    # COMPUTE
    # ---------- ----------
    vecMap_entry, vecMap_exit = vecAdd_state.add_map(
        'vecAdd_map', dict(i='0:{}'.format(n)))

    vecAdd_tasklet = vecAdd_state.add_tasklet(
        'vecAdd_task', ['x_con', 'y_con'], ['z_con'],
        'z_con = x_con + y_con')

    vecAdd_state.add_memlet_path(x_in,
                                 vecMap_entry,
                                 vecAdd_tasklet,
                                 dst_conn='x_con',
                                 memlet=dace.Memlet.simple(x_in.data, 'i'))

    vecAdd_state.add_memlet_path(y_in,
                                 vecMap_entry,
                                 vecAdd_tasklet,
                                 dst_conn='y_con',
                                 memlet=dace.Memlet.simple(y_in.data, 'i'))

    vecAdd_state.add_memlet_path(vecAdd_tasklet,
                                 vecMap_exit,
                                 z_out,
                                 src_conn='z_con',
                                 memlet=dace.Memlet.simple(
                                     z_out.data, 'i'))

    return vecAdd_sdfg


def make_nested_sdfg_cpu():
    '''
    Build an SDFG with two nested SDFGs
    '''

    n = dace.symbol("n")
    m = dace.symbol("m")

    sdfg = dace.SDFG("two_vecAdd")
    state = sdfg.add_state("state")

    # build the first axpy: works with x,y, and z of n-elements
    access_nodes_dict = {"x": "x", "y": "y", "z": "z"}

    # ATTENTION: this two nested SDFG must have the same name as they are equal
    to_nest = make_vecAdd_sdfg("n", "vecAdd", access_nodes_dict)

    sdfg.add_array("x", [n], dace.float32)
    sdfg.add_array("y", [n], dace.float32)
    sdfg.add_array("z", [n], dace.float32)
    x = state.add_read("x")
    y = state.add_read("y")
    z = state.add_write("z")

    nested_sdfg = state.add_nested_sdfg(to_nest, sdfg, {"x", "y"},{"z"})

    state.add_memlet_path(x,
                          nested_sdfg,
                          dst_conn="x",
                          memlet=Memlet.simple(x, "0:n", num_accesses=n))
    state.add_memlet_path(y,
                          nested_sdfg,
                          dst_conn="y",
                          memlet=Memlet.simple(y, "0:n", num_accesses=n))
    state.add_memlet_path(nested_sdfg,
                          z,
                          src_conn="z",
                          memlet=Memlet.simple(z, "0:n", num_accesses=n))


    # Build the second axpy: works with v,w and u of m elements
    access_nodes_dict = {"x": "v", "y": "w", "z": "u"}
    to_nest = make_vecAdd_sdfg("m", "vecAdd", access_nodes_dict)

    sdfg.add_array("v", [m], dace.float32)
    sdfg.add_array("w", [m], dace.float32)
    sdfg.add_array("u", [m], dace.float32)
    v = state.add_read("v")
    w = state.add_read("w")
    u = state.add_write("u")

    nested_sdfg = state.add_nested_sdfg(to_nest, sdfg, {"v", "w"}, {"u"})

    state.add_memlet_path(v,
                          nested_sdfg,
                          dst_conn="v",
                          memlet=Memlet.simple(v, "0:m", num_accesses=m))
    state.add_memlet_path(w,
                          nested_sdfg,
                          dst_conn="w",
                          memlet=Memlet.simple(w, "0:m", num_accesses=m))
    state.add_memlet_path(nested_sdfg,
                          u,
                          src_conn="u",
                          memlet=Memlet.simple(u, "0:m", num_accesses=m))

    return sdfg


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("N", type=int, nargs="?", default=32)
    parser.add_argument("M", type=int, nargs="?", default=64)
    args = vars(parser.parse_args())

    size_n = args["N"]
    size_m = args["M"]
    sdfg = make_nested_sdfg_cpu()


    sdfg.save("two_axpy.sdfg")


    two_axpy = sdfg.compile()

    x = np.random.rand(size_n).astype(np.float32)
    y = np.random.rand(size_n).astype(np.float32)
    z = np.random.rand(size_n).astype(np.float32)

    v = np.random.rand(size_m).astype(np.float32)
    w = np.random.rand(size_m).astype(np.float32)
    u = np.random.rand(size_m).astype(np.float32)

    two_axpy(x=x, y=y, z=z, v=v, w=w, u=u, n=size_n, m=size_m)

    ref1 = np.add(x, y)
    ref2 = np.add(v, w)

    diff1 = np.linalg.norm(ref1 - z) / size_n
    diff2 = np.linalg.norm(ref2 - u) / size_m
    print("Difference:", diff1)
    if diff1 <= 1e-5 and diff2 <= 1e-5:
        print("==== Program end ====")
    else:
        print("==== Program Error! ====")

    # There is no need to check that the Nested SDFG has been generated only once. If this is not the case
    # the test will fail while compiling

    exit(0 if diff1 <= 1e-5 or diff2 <= 1e-5 else 1)
