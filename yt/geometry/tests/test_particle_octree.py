"""
Tests for particle octree



"""

#-----------------------------------------------------------------------------
# Copyright (c) 2013, yt Development Team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING.txt, distributed with this software.
#-----------------------------------------------------------------------------


import numpy as np
import time
import os
import copy

from yt.frontends.stream.data_structures import load_particles
from yt.geometry.oct_container import \
    OctreeContainer
from yt.geometry.particle_oct_container import \
    ParticleOctreeContainer, \
    ParticleBitmap
from yt.geometry.oct_container import _ORDER_MAX
from yt.geometry.selection_routines import RegionSelector, AlwaysSelector
from yt.testing import \
    assert_equal, \
    requires_file, \
    assert_true, \
    assert_array_equal
from yt.units.unit_registry import UnitRegistry
from yt.units.yt_array import YTArray
from yt.utilities.lib.geometry_utils import get_morton_indices, \
    get_morton_points, \
    get_hilbert_points, \
    get_hilbert_indices

import yt.units.dimensions as dimensions
import yt.data_objects.api

NPART = 32**3
DLE = np.array([0.0, 0.0, 0.0])
DRE = np.array([10.0, 10.0, 10.0])
DW = (DRE-DLE)
dx = DW/(2**_ORDER_MAX)

def test_add_particles_random():
    np.random.seed(int(0x4d3d3d3))
    pos = np.random.normal(0.5, scale=0.05, size=(NPART,3)) * (DRE-DLE) + DLE
    # Now convert to integers
    for i in range(3):
        np.clip(pos[:,i], DLE[i], DRE[i], pos[:,i])
    # Convert to integers
    pos = np.floor((pos - DLE)/dx).astype("uint64")
    morton = get_morton_indices(pos)
    morton.sort()
    for ndom in [1, 2, 4, 8]:
        octree = ParticleOctreeContainer((1, 1, 1), DLE, DRE)
        octree.n_ref = 32
        for dom, split in enumerate(np.array_split(morton, ndom)):
            octree.add(split)
        octree.finalize()
        # This visits every oct.
        tc = octree.recursively_count()
        total_count = np.zeros(len(tc), dtype="int32")
        for i in sorted(tc):
            total_count[i] = tc[i]
        yield assert_equal, octree.nocts, total_count.sum()
        # This visits every cell -- including those covered by octs.
        #for dom in range(ndom):
        #    level_count += octree.count_levels(total_count.size-1, dom, mask)
        yield assert_equal, total_count, [1, 8, 64, 64, 256, 536, 1856, 1672]

def test_save_load_octree():
    np.random.seed(int(0x4d3d3d3))
    pos = np.random.normal(0.5, scale=0.05, size=(NPART,3)) * (DRE-DLE) + DLE
    octree = ParticleOctreeContainer((1, 1, 1), DLE, DRE)
    octree.n_ref = 32
    for i in range(3):
        np.clip(pos[:,i], DLE[i], DRE[i], pos[:,i])
    # Convert to integers
    pos = np.floor((pos - DLE)/dx).astype("uint64")
    morton = get_morton_indices(pos)
    morton.sort()
    octree.add(morton)
    octree.finalize()
    saved = octree.save_octree()
    loaded = OctreeContainer.load_octree(saved)
    always = AlwaysSelector(None)
    ir1 = octree.ires(always)
    ir2 = loaded.ires(always)
    yield assert_equal, ir1, ir2

    fc1 = octree.fcoords(always)
    fc2 = loaded.fcoords(always)
    yield assert_equal, fc1, fc2

    fw1 = octree.fwidth(always)
    fw2 = loaded.fwidth(always)
    yield assert_equal, fw1, fw2

def test_particle_octree_counts():
    np.random.seed(int(0x4d3d3d3))
    # Eight times as many!
    data = {}
    bbox = []
    for i, ax in enumerate('xyz'):
        DW = DRE[i] - DLE[i]
        LE = DLE[i]
        data["particle_position_%s" % ax] = \
            np.random.normal(0.5, scale=0.05, size=(NPART*8)) * DW + LE
        bbox.append( [DLE[i], DRE[i]] )
    bbox = np.array(bbox)
    for n_ref in [16, 32, 64, 512, 1024]:
        ds = load_particles(data, 1.0, bbox = bbox, n_ref = n_ref)
        dd = ds.all_data()
        bi = dd["io","mesh_id"]
        v = np.bincount(bi.astype("intp"))
        yield assert_equal, v.max() <= n_ref, True
        bi2 = dd["all","mesh_id"]
        yield assert_equal, bi, bi2

def test_particle_overrefine():
    np.random.seed(int(0x4d3d3d3))
    data = {}
    bbox = []
    for i, ax in enumerate('xyz'):
        DW = DRE[i] - DLE[i]
        LE = DLE[i]
        data["particle_position_%s" % ax] = \
            np.random.normal(0.5, scale=0.05, size=(NPART)) * DW + LE
        bbox.append( [DLE[i], DRE[i]] )
    bbox = np.array(bbox)
    _attrs = ('icoords', 'fcoords', 'fwidth', 'ires')
    for n_ref in [16, 32, 64, 512, 1024]:
        ds1 = load_particles(data, 1.0, bbox = bbox, n_ref = n_ref)
        dd1 = ds1.all_data()
        v1 = dict((a, getattr(dd1, a)) for a in _attrs)
        cv1 = dd1["cell_volume"].sum(dtype="float64")
        for over_refine in [1, 2, 3]:
            f = 1 << (3*(over_refine-1))
            ds2 = load_particles(data, 1.0, bbox = bbox, n_ref = n_ref,
                                over_refine_factor = over_refine)
            dd2 = ds2.all_data()
            v2 = dict((a, getattr(dd2, a)) for a in _attrs)
            for a in sorted(v1):
                yield assert_equal, v1[a].size * f, v2[a].size
            cv2 = dd2["cell_volume"].sum(dtype="float64")
            yield assert_equal, cv1, cv2

class FakeDS:
    domain_left_edge = None
    domain_right_edge = None
    domain_width = None
    unit_registry = UnitRegistry()
    unit_registry.add('code_length', 1.0, dimensions.length)
    periodicity = (False, False, False)

class FakeRegion:
    def __init__(self, nfiles):
        self.ds = FakeDS()
        self.ds.domain_left_edge = YTArray([0.0, 0.0, 0.0], "code_length",
                                           registry=self.ds.unit_registry)
        self.ds.domain_right_edge = YTArray([nfiles, nfiles, nfiles], "code_length",
                                            registry=self.ds.unit_registry)
        self.ds.domain_width = self.ds.domain_right_edge - \
                               self.ds.domain_left_edge
        self.nfiles = nfiles

    def set_edges(self, file_id, dx = 0.1):
        self.left_edge = YTArray([file_id + dx, 0.0, 0.0],
                                 'code_length', registry=self.ds.unit_registry)
        self.right_edge = YTArray([file_id+1 - dx, self.nfiles, self.nfiles],
                                  'code_length', registry=self.ds.unit_registry)

class FakeBoxRegion:
    def __init__(self, nfiles, DLE, DRE):
        self.ds = FakeDS()
        self.ds.domain_left_edge = YTArray(DLE, "code_length",
                                           registry=self.ds.unit_registry)
        self.ds.domain_right_edge = YTArray(DRE, "code_length",
                                            registry=self.ds.unit_registry)
        self.ds.domain_width = self.ds.domain_right_edge - \
                               self.ds.domain_left_edge
        self.nfiles = nfiles

    def set_edges(self, center, width):
        self.left_edge = self.ds.domain_left_edge + self.ds.domain_width*(center-width/2)
        self.right_edge = self.ds.domain_left_edge + self.ds.domain_width*(center+width/2)


def fake_decomp_random(npart, nfiles, ifile, DLE, DRE,
                       buff=0.0, verbose=False):
    np.random.seed(int(0x4d3d3d3)+ifile)
    DW = DRE - DLE
    nPF = int(npart/nfiles)
    nR = npart % nfiles
    if verbose: print("{}/{} remainder particles put in first file".format(nR,npart))
    if ifile == 0:
        pos = np.random.normal(0.5, scale=0.05, size=(nPF+nR,3))*DW + DLE
    else:
        pos = np.random.normal(0.5, scale=0.05, size=(nPF,3))*DW + DLE
    for i in range(3):
        np.clip(pos[:,i], DLE[i], DRE[i], pos[:,i])
    return pos

def fake_decomp_sliced(npart, nfiles, ifile, DLE, DRE,
                       buff=0.0, verbose=False):
    np.random.seed(int(0x4d3d3d3)+ifile)
    DW = DRE - DLE
    div = DW/nfiles
    nPF = int(npart/nfiles)
    nR = npart % nfiles
    if verbose: print("{}/{} remainder particles put in first file".format(nR,npart))
    inp = nPF
    if ifile == 0: inp += nR
    iLE = DLE[0] + ifile*div[0]
    iRE = iLE + div[0]
    if ifile != 0:
        iLE -= buff*div[0]
    if ifile != (nfiles-1):
        iRE += buff*div[0]
    pos = np.empty((inp,3), dtype='float')
    pos[:,0] = np.random.uniform(iLE, iRE, inp)
    for i in range(1,3):
        pos[:,i] = np.random.uniform(DLE[i], DRE[i], inp)
    return pos

def filter_decomp_hilbert_gaussian(npart, nfiles, DLE, DRE,
                                   fname_base=None):
    import pickle
    np.random.seed(int(0x4d3d3d3))
    DW = DRE - DLE
    if fname_base is None:
        fname_base = 'hilbert{}_gaussian_np{}_nf{}_'.format(order,npart,nfiles)
    def load_pos(file_id):
        filename = fname_base+'file{}'.format(file_id)
        if os.path.isfile(filename):
            fd = open(filename,'rb')
            positions = pickle.load(fd)
            fd.close()
        else:
            positions = np.empty((0,3), dtype='float64')
        return positions
    def save_pos(file_id,positions):
        filename = fname_base+'file{}'.format(file_id)
        fd = open(filename,'wb')
        pickle.dump(positions,fd)
        fd.close()
    # Random
    for ifile in range(nfiles):
        print 'Fixing file {}'.format(ifile)
        # print 'Random, file {}'.format(ifile)
        ipos = load_pos(ifile)
        for k in range(3):
            ipos[:,k] += 0.999999*DW[k]
            idx = (ipos[:,k]>=DRE[k])
            ipos[idx,k] -= (1.0e-9)*DW[k]
        save_pos(ifile,ipos)


def makeall_decomp_hilbert_gaussian(npart, nfiles, DLE, DRE,
                                    buff=0.0, order=6, verbose=False,
                                    fname_base=None, nchunk=10,
                                    width=None, center=None,
                                    frac_random=0.1):
    import pickle
    np.random.seed(int(0x4d3d3d3))
    DW = DRE - DLE
    if fname_base is None:
        fname_base = 'hilbert{}_gaussian_np{}_nf{}_'.format(order,npart,nfiles)
    if width is None:
        width = 0.1*DW
    if center is None:
        center = DLE+0.5*DW
    def load_pos(file_id):
        filename = fname_base+'file{}'.format(file_id)
        if os.path.isfile(filename):
            fd = open(filename,'rb')
            positions = pickle.load(fd)
            fd.close()
        else:
            positions = np.empty((0,3), dtype='float64')
        return positions
    def save_pos(file_id,positions):
        filename = fname_base+'file{}'.format(file_id)
        fd = open(filename,'wb')
        pickle.dump(positions,fd)
        fd.close()
    npart_rnd = int(frac_random*npart)
    npart_gau = npart - npart_rnd
    dim_hilbert = (1<<order)
    nH = dim_hilbert**3
    if nH < nfiles:
        raise ValueError('Fewer hilbert cells than files.')
    nHPF = nH/nfiles
    rHPF = nH%nfiles
    hdiv = DW/dim_hilbert
    for ichunk in range(nchunk):
        print "Chunk {}...".format(ichunk)
        inp = npart_gau/nchunk
        if ichunk == 0: inp += (npart_gau % nchunk)
        pos = np.empty((inp,3), dtype='float64')
        ind = np.empty((inp,3), dtype='int64')
        for k in range(3):
            pos[:,k] = np.clip(np.random.normal(center[k], width[k], inp),
                               DLE[k], DRE[k]-(1.0e-9)*DW[k])
            ind[:,k] = (pos[:,k]-DLE[k])/(DW[k]/dim_hilbert)
        harr = get_hilbert_indices(order, ind)
        farr = (harr-rHPF)/nHPF
        for ifile in range(nfiles):
            print "Chunk {}, file {}...".format(ichunk,ifile)
            ipos = load_pos(ifile)
            if ifile == 0:
                idx = (farr <= ifile) # Put remainders in first file
            else:
                idx = (farr == ifile)
            ipos = np.concatenate((ipos,pos[idx,:]),axis=0)
            save_pos(ifile,ipos)
    # Random
    print "Random..."
    for ifile in range(nfiles):
        # print 'Random, file {}'.format(ifile)
        ipos = load_pos(ifile)
        ipos_rnd = fake_decomp_hilbert_uniform(npart_rnd, nfiles, ifile, DLE, DRE,
                                               buff=buff, order=order, verbose=verbose)
        ipos = np.concatenate((ipos,ipos_rnd),axis=0)
        save_pos(ifile,ipos)
    
def fake_decomp_hilbert_gaussian(npart, nfiles, ifile, DLE, DRE,
                                 buff=0.0, order=6, verbose=False,
                                 fname=None):
    np.random.seed(int(0x4d3d3d3))
    DW = DRE - DLE
    dim_hilbert = (1<<order)
    nH = dim_hilbert**3
    if nH < nfiles:
        raise Exception('Fewer hilbert cells than files.')
    nHPF = nH/nfiles
    rHPF = nH%nfiles
    hdiv = DW/dim_hilbert
    if ifile == 0:
        hlist = np.arange(0,nHPF+rHPF, dtype='int64')
    else:
        hlist = np.arange(ifile*nHPF+rHPF,(ifile+1)*nHPF+rHPF, dtype='int64')
    hpos = get_hilbert_points(order, hlist)
    iLE = np.empty((len(hlist),3), dtype='float')
    iRE = np.empty((len(hlist),3), dtype='float')
    count = np.zeros(3,dtype='int')
    pos = np.empty((npart,3), dtype='float')
    for k in range(3):
        iLE[:,k] = DLE[k] + hdiv[k]*hpos[:,k]
        iRE[:,k] = iLE[:,k] + hdiv[k]
        iLE[hpos[:,k]!=0,k] -= buff*hdiv[k]
        iRE[hpos[:,k]!=(dim_hilbert-1),k] += buff*hdiv[k]
        print 'sampling'
        gpos = np.clip(np.random.normal(DLE[k]+DW[k]/2.0, DW[k]/10.0, npart),
                       DLE[k], DRE[k])
        print 'sampled'
        # for p in range(npart):
        #     ipos = np.clip(np.random.normal(DLE[k]+DW[k]/2.0, DW[k]/10.0, 1),
        #                    DLE[k], DRE[k])
        for p,ipos in enumerate(gpos):
            if (p%(10**8))==0: print "    dim {}, part {} ({})".format(k,p,ipos)
            for i in range(len(hlist)):
                if iLE[i,k] <= ipos < iRE[i,k]:
                    pos[count[k],k] = ipos
                    count[k] += 1
                    break
    return pos[:count.min(),:]
    
def fake_decomp_hilbert_uniform(npart, nfiles, ifile, DLE, DRE,
                                buff=0.0, order=6, verbose=False):
    np.random.seed(int(0x4d3d3d3)+ifile)
    DW = DRE - DLE
    dim_hilbert = (1<<order)
    nH = dim_hilbert**3
    if nH < nfiles:
        raise Exception('Fewer hilbert cells than files.')
    nHPF = nH/nfiles
    rHPF = nH%nfiles
    nPH = npart/nH
    nRH = npart%nH
    hind = np.arange(nH, dtype='int64')
    hpos = get_hilbert_points(order, hind)
    hdiv = DW/dim_hilbert
    if ifile == 0:
        hlist = range(0,nHPF+rHPF)
        nptot = nPH*len(hlist)+nRH
    else:
        hlist = range(ifile*nHPF+rHPF,(ifile+1)*nHPF+rHPF)
        nptot = nPH*len(hlist)
    pos = np.empty((nptot,3), dtype='float')
    pc = 0
    for i in hlist:
        iLE = DLE + hdiv*hpos[i,:]
        iRE = iLE + hdiv
        for k in range(3): # Don't add buffer past domain bounds
            if hpos[i,k] != 0:
                iLE[k] -= buff*hdiv[k]
            if hpos[i,k] != (dim_hilbert-1):
                iRE[k] += buff*hdiv[k]
        inp = nPH
        if (ifile == 0) and (i == 0): inp += nRH
        for k in range(3):
            pos[pc:(pc+inp),k] = np.random.uniform(iLE[k], iRE[k], inp)
        pc += inp
    return pos

def fake_decomp_morton(npart, nfiles, ifile, DLE, DRE,
                        buff=0.0, order=6, verbose=False):
    np.random.seed(int(0x4d3d3d3)+ifile)
    DW = DRE - DLE
    dim_morton = (1<<order)
    nH = dim_morton**3
    if nH < nfiles:
        raise Exception('Fewer morton cells than files.')
    nHPF = nH/nfiles
    rHPF = nH%nfiles
    nPH = npart/nH
    nRH = npart%nH
    hind = np.arange(nH, dtype='uint64')
    hpos = get_morton_points(hind)
    hdiv = DW/dim_morton
    if ifile == 0:
        hlist = range(0,nHPF+rHPF)
        nptot = nPH*len(hlist)+nRH
    else:
        hlist = range(ifile*nHPF+rHPF,(ifile+1)*nHPF+rHPF)
        nptot = nPH*len(hlist)
    pos = np.empty((nptot,3), dtype='float')
    pc = 0
    for i in hlist:
        iLE = DLE + hdiv*hpos[i,:]
        iRE = iLE + hdiv
        for k in range(3): # Don't add buffer past domain bounds
            if hpos[i,k] != 0:
                iLE[k] -= buff*hdiv[k]
            if hpos[i,k] != (dim_morton-1):
                iRE[k] += buff*hdiv[k]
        inp = nPH
        if (ifile == 0) and (i == 0): inp += nRH
        for k in range(3):
            pos[pc:(pc+inp),k] = np.random.uniform(iLE[k], iRE[k], inp)
        pc += inp
    return pos

def fake_decomp_grid(npart, nfiles, ifile, DLE, DRE, verbose=False):
    # TODO: handle 'remainder' particles
    np.random.seed(int(0x4d3d3d3)+ifile)
    DW = DRE - DLE
    nYZ = int(np.sqrt(npart/nfiles))
    nR = npart - nYZ*nYZ*nfiles
    div = DW/nYZ
    Y, Z = np.mgrid[DLE[1] + 0.1*div[1] : DRE[1] - 0.1*div[1] : nYZ * 1j,
                    DLE[2] + 0.1*div[2] : DRE[2] - 0.1*div[2] : nYZ * 1j]
    X = 0.5 * div[0] * np.ones(Y.shape, dtype="float64") + div[0]*ifile
    pos = np.array([X.ravel(),Y.ravel(),Z.ravel()],
                   dtype="float64").transpose()
    return pos

def yield_fake_decomp(decomp, npart, nfiles, DLE, DRE, **kws):
    for ifile in range(nfiles):
        yield fake_decomp(decomp, npart, nfiles, ifile, DLE, DRE, **kws)

def fake_decomp(decomp, npart, nfiles, ifile, DLE, DRE, 
                distrib='uniform', fname=None, **kws):
    import pickle
    if fname is None and distrib == 'gaussian':
        fname = '{}6_{}_np{}_nf{}_file{}'.format(decomp,distrib,npart,nfiles,ifile)
    if fname is not None and os.path.isfile(fname):
        fd = open(fname,'rb')
        pos = pickle.load(fd)
        fd.close()
        filter_decomp_hilbert_gaussian(npart, nfiles, DLE, DRE,
                                       fname_base=fname.split('file')[0])
        raise Exception('Stop')
        return pos
    if decomp.startswith('zoom_'):
        zoom_factor = 5
        decomp_zoom = decomp.split('zoom_')[-1]
        zoom_npart = npart/2
        zoom_rem = npart%2
        pos1 = fake_decomp(decomp_zoom, zoom_npart+zoom_rem, 
                           nfiles, ifile, DLE, DRE, distrib=distrib, **kws)
        DLE_zoom = DLE + 0.5*DW*(1.0 - 1.0/float(zoom_factor))
        DRE_zoom = DLE_zoom + DW/zoom_factor
        pos2 = fake_decomp(decomp_zoom, zoom_npart, nfiles, ifile,
                                  DLE_zoom, DRE_zoom, distrib=distrib, **kws)
        pos = np.concatenate((pos1,pos2),axis=0)
    elif '_' in decomp:
        decomp_list = decomp.split('_')
        decomp_np = npart/len(decomp_list)
        decomp_nr = npart%len(decomp_list)
        pos = np.empty((0,3), dtype='float')
        for i,idecomp in enumerate(decomp_list):
            inp = decomp_np
            if i == 0:
                inp += decomp_nr
            ipos = fake_decomp(idecomp, inp, nfiles, ifile, DLE, DRE, 
                               distrib=distrib, **kws)
            pos = np.concatenate((pos,ipos),axis=0)
    # A perfect grid, no overlap between files
    elif decomp == 'grid':
        buff = kws.pop('buff',None)
        pos = fake_decomp_grid(npart, nfiles, ifile, DLE, DRE, **kws)
    # Completely random data set
    elif decomp == 'random':
        if distrib == 'uniform':
            pos = fake_decomp_random(npart, nfiles, ifile, DLE, DRE, **kws)
        else:
            raise ValueError("Unsupported value for input parameter 'distrib'".format(distrib))
    # Each file contains a slab (part of x domain, all of y/z domain)
    elif decomp == 'sliced':
        if distrib == 'uniform':
            pos = fake_decomp_sliced(npart, nfiles, ifile, DLE, DRE, **kws)
        else:
            raise ValueError("Unsupported value for input parameter 'distrib'".format(distrib))
    # Particles are assigned to files based on their location on a
    # Peano-Hilbert curve of order 6
    elif decomp.startswith('hilbert'):
        if decomp == 'hilbert':
            kws['order'] = 6
        else:
            kws['order'] = int(decomp.split('hilbert')[-1])
        if distrib == 'uniform':
            pos = fake_decomp_hilbert_uniform(npart, nfiles, ifile, DLE, DRE, **kws)
        elif distrib == 'gaussian':
            makeall_decomp_hilbert_gaussian(npart, nfiles, DLE, DRE, 
                                            fname_base=fname.split('file')[0], **kws)
            pos = fake_decomp(decomp, npart, nfiles, ifile, DLE, DRE,
                              distrib=distrib, fname=fname, **kws)
            # pos = fake_decomp_hilbert_gaussian(npart, nfiles, ifile, DLE, DRE, **kws)
        else:
            raise ValueError("Unsupported value for input parameter 'distrib'".format(distrib))
    # Particles are assigned to files based on their location on a
    # Morton ordered Z-curve of order 6
    elif decomp.startswith('morton'):
        if decomp == 'morton':
            kws['order'] = 6
        else:
            kws['order'] = int(decomp.split('morton')[-1])
        if distrib == 'uniform':
            pos = fake_decomp_morton(npart, nfiles, ifile, DLE, DRE, **kws)
        else:
            raise ValueError("Unsupported value for input parameter 'distrib'".format(distrib))
    else:
        raise ValueError("Unsupported value {} for input parameter 'decomp'".format(decomp))
    # Save
    if fname is not None:
        fd = open(fname,'wb')
        pickle.dump(pos,fd)
        fd.close()
    return pos

def FakeBitmap(npart, nfiles, order1, order2, decomp='grid', 
               buff=0.5, DLE=None, DRE=None, distrib='uniform',
               fname=None, verbose=False, really_verbose=False):
    from yt.funcs import get_pbar
    N = (1<<order1)
    if DLE is None: DLE = np.array([0.0, 0.0, 0.0])
    if DRE is None: DRE = np.array([1.0, 1.0, 1.0])
    reg = ParticleBitmap(DLE, DRE, nfiles,
                         index_order1 = order1,
                         index_order2 = order2)
    # Load from file if it exists
    if isinstance(fname,str) and os.path.isfile(fname):
        reg.load_bitmasks(fname)
        cc = reg.find_collisions_coarse(verbose=verbose)
        rc = reg.find_collisions_refined(verbose=verbose)
    else:
        # Create positions for each file
        posgen = yield_fake_decomp(decomp, npart, nfiles, DLE, DRE, buff=buff,
                                   distrib=distrib, verbose=really_verbose)
        # Coarse index
        cp = 0
        pb = get_pbar("Initializing coarse index ",nfiles)
        max_npart = 0
        for i,pos in enumerate(posgen):
            pb.update(i)
            reg._coarse_index_data_file(pos, i)
            max_npart = max(max_npart, pos.shape[0])
            cp += pos.shape[0]
        pb.finish()
        if i != (nfiles-1):
            raise RuntimeError("There are positions for {} files, but there should be {}.".format(i+1,nfiles))
        if really_verbose: print("{} particles in total".format(cp))
        cc = reg.find_collisions_coarse(verbose=verbose)
        # Refined index
        sub_mi1 = np.zeros(max_npart, "uint64")
        sub_mi2 = np.zeros(max_npart, "uint64")
        posgen = yield_fake_decomp(decomp, npart, nfiles, DLE, DRE, buff=buff, 
                                   distrib=distrib, verbose=really_verbose)
        pb = get_pbar("Initializing refined index ",nfiles)
        for i,pos in enumerate(posgen):
            pb.update(i)
            reg._refined_index_data_file(pos,
                                         reg.masks.sum(axis=1).astype('uint8'),
                                         sub_mi1, sub_mi2, i)
        pb.finish()
        rc = reg.find_collisions_refined(verbose=verbose)
        # Owners
        reg.set_owners()
        # Save if file name provided
        if isinstance(fname,str):
            reg.save_bitmasks(fname=fname)
    mem = reg.calcsize_bitmasks()
    return reg, cc, rc, mem

def vary_selection_stats(var, varlist, verbose=False, plot=False,
                         nfiles=512, npart_dim=1024, 
                         DLE = [0.0, 0.0, 0.0],
                         DRE = [1.0, 1.0, 1.0], 
                         overwrite=False, extendtag=None, **kws):
    import pickle
    kwsDEF = dict(decomp='hilbert',
                  buff=0.1,
                  distrib='uniform',
                  ngz=0,
                  nreps=10)
    for k in kwsDEF: kws.setdefault(k,kwsDEF[k])
    testtag = "vary_{}_np{}_nf{}_{}_buff{}_{}ngz_{}reps".format(var,npart_dim,nfiles,
                                                                kws['decomp'],str(kws['buff']).replace('.','p'),
                                                                kws['ngz'],kws['nreps'])
    if kws['distrib'] != 'uniform':
        testtag += '_{}'.format(kws['distrib'])
    if extendtag is not None:
        testtag += extendtag
    fname = testtag+'.dat'
    # Create regions
    fake_regions = []
    if var == 'selector':
        for v in varlist:
            fr = FakeBoxRegion(nfiles, DLE, DRE)
            fr.set_edges(0.5,v)
            fake_regions.append(fr)
    else:
        for c,r in [(0.5,0.1),(0.3,0.1),(0.5,0.01),(0.5,0.2),(0.5,0.5),(0.5,1.0)]:
            fr = FakeBoxRegion(nfiles, DLE, DRE)
            fr.set_edges(c,r)
            fake_regions.append(fr)
    # Load
    if os.path.isfile(fname) and not overwrite:
        fd = open(fname,'rb')
        out = pickle.load(fd)
        fd.close()
    else:
        out = {}
    # Get stats
    if verbose: print("Timing differences due to '{}'".format(var))
    if var == 'selector':
        iout = time_selection(npart_dim, nfiles, fake_regions, 
                              verbose=verbose, total_regions=False,
                              **copy.copy(kws))
        outkws = iout.keys()
        for i,v in enumerate(varlist):
            out[v] = {}
            for k in outkws: out[v][k] = iout[k][i]
            if verbose: print("{var} = {v}: {tm} s, {ndf}/{nf} files, {ngf}/{nf} ghost files".format(var=var,v=v,**out[v]))
    else:
        for v in varlist:
            if v in out: continue
            kws[var] = v
            out[v] = time_selection(npart_dim, nfiles, fake_regions, 
                                    verbose=verbose, total_regions=True,
                                    **copy.copy(kws))
            if verbose: print("{var} = {v}: {tm} s, {ndf}/{nf} files, {ngf}/{nf} ghost files".format(var=var,v=v,**out[v]))
    # Save
    fd = open(fname,'wb')
    pickle.dump(out,fd)
    fd.close()
    # Plot
    if plot:
        plotfile = os.path.join(os.getcwd(),testtag+'.png')
        plot_vary_selection_stats(var, varlist, out, fname=plotfile)
    return out

def plot_vary_selection_stats(var, varlist, result, fname=None):
    import matplotlib.pyplot as plt
    Nvar = len(varlist)
    t = np.empty(Nvar, dtype='float')
    df = np.empty(Nvar, dtype='float')
    gf = np.empty(Nvar, dtype='float')
    nf = np.empty(Nvar, dtype='float')
    cc = np.empty(Nvar, dtype='float')
    rc = np.empty(Nvar, dtype='float')
    for i,v in enumerate(varlist):
        t[i] = result[v]['tm']
        df[i] = result[v]['ndf']
        gf[i] = result[v]['ngf']
        nf[i] = result[v]['nf']
        cc[i] = float(result[v]['cc'][0])/float(result[v]['cc'][1])
        rc[i] = float(result[v]['rc'][0])/float(result[v]['rc'][1])
    # Plot
    plt.close('all')
    f, ax1 = plt.subplots()
    if var == 'decomp':
        ax1.scatter(range(Nvar),t,c='k',marker='o',s=50,label='Time')
    elif var == 'selector':
        ax1.semilogx(varlist,t,'k-',label='Time')
    elif var in ['order1','order2']:
        ax1.semilogy(varlist,t,'k-',label='Time')
    else:
        ax1.plot(varlist,t,'k-',label='Time')
    ax1.set_xlabel(var)
    ax1.set_ylabel('Time (s)')
    for axis in ['top','bottom','left','right']:
        ax1.spines[axis].set_linewidth(4)
    ax1.tick_params(width=4)
    # Files and collitions
    ax2 = ax1.twinx()
    ax2.set_ylabel('% files/collisions')
    if var == 'decomp':
        ax2.scatter(range(Nvar),df/nf,c='b',marker='^',s=50,label='Primary Files')
        ax2.scatter(range(Nvar),gf/nf,c='b',marker='s',s=50,label='Ghost Files')
        ax2.scatter(range(Nvar),cc,c='r',marker='>',s=50,label='Coarse Collisions')
        ax2.scatter(range(Nvar),rc,c='r',marker='<',s=50,label='Refined Collisions')
        xticks = ax2.set_xticklabels(['']+varlist)
        plt.setp(xticks, rotation=45, fontsize=10)
    elif var == 'selector':
        ax2.semilogx(varlist,df/nf,'b-',label='Primary Files')
        ax2.semilogx(varlist,gf/nf,'b--',label='Ghost Files')
        ax2.semilogx(varlist,cc,'r-',label='Coarse Collisions')
        ax2.semilogx(varlist,rc,'r--',label='Refined Collisions')
    else:
        ax2.plot(varlist,df/nf,'b-',label='Primary Files')
        ax2.plot(varlist,gf/nf,'b--',label='Ghost Files')
        ax2.plot(varlist,cc,'r-',label='Coarse Collisions')
        ax2.plot(varlist,rc,'r--',label='Refined Collisions')
    plt.legend(loc=3,bbox_to_anchor=(0., 1.02, 1., .102),
               ncol=3,mode="expand", borderaxespad=0.)
    for axis in ['top','bottom','left','right']:
        ax2.spines[axis].set_linewidth(4)
    ax2.tick_params(width=4)
    # Save
    plt.savefig(fname)
    print(fname)

def time_selection(npart_dim, nfiles, fake_regions, 
                   verbose=False, really_verbose=False,
                   decomp='hilbert', order1=6, order2=4, ngz=0,
                   buff=0.5, total_order=10, distrib='uniform',
                   total_regions=True,nreps=10):
    # Set order
    if order2 is None:
        if order1 is None:
            order1 = total_order/2
        order2 = total_order - order1
    elif order1 is None:
        order1 = total_order - order2
    # File name
    fname = "bitmap_{}_np{}_nf{}_oc{}_or{}_buff{}".format(decomp,npart_dim,nfiles,
                                                          order1,order2,
                                                          str(buff).replace('.','p'))
    if distrib != 'uniform':
        fname += '_{}'.format(distrib)
    # Fake bitmap
    npart = npart_dim**3
    reg, cc, rc, mem = FakeBitmap(npart, nfiles, order1, order2, decomp=decomp, 
                                  buff=buff, distrib=distrib, fname=fname,
                                  verbose=verbose, really_verbose=really_verbose)
    if total_regions:
        times = np.empty(nreps,dtype='float')
        for k in range(nreps):
            ndf = 0
            ngf = 0
            nf = 0
            times[k] = 0.0
            for fr in fake_regions:
                selector = RegionSelector(fr)
                t1 = time.time()
                df, gf = reg.identify_data_files(selector, ngz=ngz)
                t2 = time.time()
                ndf += len(df)
                ngf += len(gf)
                nf += nfiles
                times[k] += t2-t1
        tt = np.sum(times)
        tm = np.mean(times)
        ts = np.std(times)
    else:
        Nfr = len(fake_regions)
        ndf = np.empty(Nfr, dtype='int32')
        ngf = np.empty(Nfr, dtype='int32')
        nf = np.empty(Nfr, dtype='int32')
        tt = np.empty(Nfr, dtype='float')
        tm = np.empty(Nfr, dtype='float')
        ts = np.empty(Nfr, dtype='float')
        cc = Nfr*[cc]
        rc = Nfr*[rc]
        mem = Nfr*[mem]
        times = np.empty(nreps,dtype='float')
        for i,fr in enumerate(fake_regions):
            selector = RegionSelector(fr)
            for k in range(nreps):
                t1 = time.time()
                df, gf = reg.identify_data_files(selector, ngz=ngz)
                t2 = time.time()
                times[k] = t2-t1
            tt[i] = np.sum(times)
            tm[i] = np.mean(times)
            ts[i] = np.std(times)
            ndf[i] = len(df)
            ngf[i] = len(gf)
            nf[i] = nfiles
    out = dict(tt=tt, tm=tm, ts=ts, ndf=ndf, ngf=ngf, nf=nf, cc=cc, rc=rc, mem=mem)
    return out

def time_selection_decomp(**kws):
    vlist = ['hilbert','morton','sliced','random','zoom_hilbert']
    out = vary_selection_stats('decomp', vlist, verbose=True, **kws)

def time_selection_selector(**kws):
    vlist = np.logspace(-1,0,num=20,endpoint=True)
    # vlist = [0.01,0.02,0.05,0.1,0.2,0.3,0.4,0.5,0.75,1.0]
    out = vary_selection_stats('selector', vlist, verbose=True, **kws)

def plot_vary_selector(plot_mem=False,**kws):
    import matplotlib as mpl
    import matplotlib.pyplot as plt
    lw = 5
    mpl.rc('font', weight='bold')#,family='serif')
    mpl.rc('lines',linewidth=lw)
    mpl.rc('axes',linewidth=4)
    order1 = 4
    order2 = 2
    fname = "vary_selector.png"
    # Set up plot
    plt.close('all')
    f, (ax1,ax2) = plt.subplots(2,1,sharex=True)
    ax1.set_yscale('log')
    # Loop over total_order
    for ngz in [0,1]:
        kws['ngz'] = ngz
        kws['order1'] = order1
        kws['order2'] = order2
        kws['plot'] = False
        vlist = np.logspace(-1,0,num=20,endpoint=True)
        result = vary_selection_stats('selector', vlist, verbose=True, **kws)
        Nvar = len(vlist)
        t = np.empty(Nvar, dtype='float')
        df = np.empty(Nvar, dtype='float')
        gf = np.empty(Nvar, dtype='float')
        nf = np.empty(Nvar, dtype='float')
        cc = np.empty(Nvar, dtype='float')
        rc = np.empty(Nvar, dtype='float')
        ts = np.empty(Nvar, dtype='float')
        mem = np.empty(Nvar, dtype='float')
        for i,v in enumerate(vlist):
            t[i] = result[v]['tm']
            df[i] = result[v]['ndf']
            gf[i] = result[v]['ngf']
            nf[i] = result[v]['nf']
            cc[i] = float(result[v]['cc'][0])/float(result[v]['cc'][1])
            rc[i] = float(result[v]['rc'][0])/float(result[v]['rc'][1])
            ts[i] = result[v].get('ts',0.0)
            mem[i] = result[v].get('mem',0.0)
        # Plot
        if ngz == 0:
            pfargs = (vlist,df/nf)
            ptkws = dict(linestyle='-',color='k',label='Time w/o Ghost Zones')
            pfkws = dict(linestyle='-',color='k',label='Primary Files')
        else:
            pfargs = (vlist,gf/nf)
            ptkws = dict(linestyle='--',color='b',label='Time w/ Ghost Zones')
            pfkws = dict(linestyle='--',color='b',label='Ghost Files')
            # pckws = dict(linestyle='-.',color='r',label='Collisions')
        if kws.get('plot_errors',False):
            ax1.errorbar(vlist,t,yerr=ts,**ptkws)
        else:
            ax1.plot(vlist,t,**ptkws)
        ax2.plot(*pfargs,**pfkws)
        # ax2.plot(vlist,cc,**pckws)
    # Formatting
    ax1.set_ylabel('Time (s)',fontsize=14, fontweight='bold')
    ax2.set_xlabel('Width of Selector', fontsize=14, fontweight='bold')
    ax2.set_ylabel('% Files Identified', fontsize=14, fontweight='bold')
    ax2.set_ylim((-0.1,1.1))
    for ax in [ax1,ax2]:
        ax.tick_params(width=4)
    plt.legend(loc=3,bbox_to_anchor=(0., 1.02, 1., .102),
               ncol=2,mode="expand", borderaxespad=0.,
               frameon=False)
    # Save
    plt.savefig(fname)
    print(fname)

def plot_vary_order1(plot_mem=False,**kws):
    import matplotlib as mpl
    import matplotlib.pyplot as plt
    lw = 5
    mpl.rc('font', weight='bold')
    mpl.rc('lines',linewidth=lw)
    mpl.rc('axes',linewidth=4)
    order2 = 0
    total_order = 7
    fname = 'vary_order1_or{}.png'.format(order2)
    # Set up plot
    plt.close('all')
    if plot_mem:
        f, (ax1,ax2,ax3) = plt.subplots(3,1,sharex=True)
        f.set_figheight(f.get_figheight()*3.0/2.0)
        ax3.set_yscale('log')
    else:
        f, (ax1,ax2) = plt.subplots(2,1,sharex=True)
    ax1.set_yscale('log')
    # Loop over total_order
    for ngz in [0,1]:
        kws['total_order'] = None
        kws['order2'] = order2
        kws['ngz'] = ngz
        kws['plot'] = False
        kws['extendtag'] = '_or{}'.format(order2)
        vlist = np.arange(1,total_order+1)
        result = vary_selection_stats('order1', vlist, verbose=True, **kws)
        Nvar = len(vlist)
        t = np.empty(Nvar, dtype='float')
        df = np.empty(Nvar, dtype='float')
        gf = np.empty(Nvar, dtype='float')
        nf = np.empty(Nvar, dtype='float')
        cc = np.empty(Nvar, dtype='float')
        rc = np.empty(Nvar, dtype='float')
        ts = np.empty(Nvar, dtype='float')
        mem = np.empty(Nvar, dtype='float')
        for i,v in enumerate(vlist):
            t[i] = result[v]['tm']
            df[i] = result[v]['ndf']
            gf[i] = result[v]['ngf']
            nf[i] = result[v]['nf']
            cc[i] = float(result[v]['cc'][0])/float(result[v]['cc'][1])
            rc[i] = float(result[v]['rc'][0])/float(result[v]['rc'][1])
            ts[i] = result[v].get('ts',0.0)
            mem[i] = result[v].get('mem',0.0)/(1024.0*1024.0) # MB
        # Plot
        if ngz == 0:
            pfargs = (vlist,df/nf)
            ptkws = dict(linestyle='-',color='k',label='Time w/o Ghost Zones')
            pfkws = dict(linestyle='-',color='k',label='Primary Files')
        else:
            pfargs = (vlist,gf/nf)
            pcargs = (vlist,cc)
            ptkws = dict(linestyle='--',color='b',label='Time w/ Ghost Zones')
            pfkws = dict(linestyle='--',color='b',label='Ghost Files')
            pckws = dict(linestyle='-.',color='r',label='Collisions')
        if kws.get('plot_errors',False):
            ax1.errorbar(vlist,t,yerr=ts,**ptkws)
        else:
            ax1.plot(vlist,t,**ptkws)
        ax2.plot(*pfargs,**pfkws)
        if ngz == 1:
            ax2.plot(*pcargs,**pckws)
        # Fitting
        if 0:
            def func_mem(x,a,b=2,c=3):#1.81906043):
                return a*(b**(c*x))
            def func_time(x,a,b=1.0,c=1.0):
                return a*(b**(c*(4**x)))
                # return a*(b**(c*0.004*(2**(2*x))))
            def func_pow(x,a,b):
                return a*(b**x)
            import scipy.optimize as optimization
            print 'fitting memory'
            # print optimization.curve_fit(func_pow, vlist, mem, np.zeros(3))
            fit_mem = optimization.curve_fit(func_mem, vlist, mem, [0.004,2.0,2.0])#,1.0])
            print fit_mem
            ax3.plot(vlist,func_mem(vlist,*fit_mem[0]),'m--')
            fit_mem = optimization.curve_fit(func_pow, vlist, mem, [0.004,4.0])
            print fit_mem
            ax3.plot(vlist,func_pow(vlist,*fit_mem[0]),'c--')
            fmem = func_pow(vlist,*fit_mem[0])
            print 'fitting time'
            fit_time = optimization.curve_fit(func_time, vlist, t, [1.0,1.0])#,1.0])
            print fit_time
            ax1.plot(vlist,func_time(vlist,*fit_time[0]),'m--')
            fit_time = optimization.curve_fit(func_pow, mem, t, [0.003,1.0])
            print fit_time
            ax1.plot(vlist,func_pow(mem,*fit_time[0]),'c--')
    # Formatting
    if plot_mem:
        ax3.plot(vlist,mem,linestyle='-',color='k')
        ax3.set_xlabel('Order of Index', fontsize=14, fontweight='bold')
        ax3.set_ylabel('Size of Index (MB)', fontsize=14, fontweight='bold')
    else:
        ax2.set_xlabel('Order of Index', fontsize=14, fontweight='bold')
    ax1.set_ylabel('Time (s)',fontsize=14, fontweight='bold')
    ax2.set_ylabel('% Files Identified/\n Cells with Collisions', fontsize=14, fontweight='bold')
    ax2.set_ylim((-0.1,1.1))
    for ax in [ax1,ax2]:
        for axis in ['top','bottom','left','right']:
            ax.spines[axis].set_linewidth(4)
        ax.tick_params(width=4)
    plt.sca(ax2)
    plt.legend(loc=3,bbox_to_anchor=(0., 1.02, 1., .102),
               ncol=3,mode="expand", borderaxespad=0.,
               frameon=False)
    # Save
    plt.savefig(fname)
    print(fname)

def plot_vary_order2(plot_mem=False, **kws):
    import matplotlib as mpl
    import matplotlib.pyplot as plt
    lw = 5
    mpl.rc('font', weight='bold')
    mpl.rc('lines',linewidth=lw)
    mpl.rc('axes',linewidth=4)
    orders = [6]
    # orders = range(2,9)
    if len(orders)>1:
        fname = 'vary_order2_mult.png'
    else:
        fname = 'vary_order2_to{}.png'.format(orders[0])
    # Set up plot
    plt.close('all')
    cmap = plt.get_cmap('jet') 
    cnorm = mpl.colors.Normalize(vmin=orders[0], vmax=orders[-1])
    smap = mpl.cm.ScalarMappable(norm=cnorm, cmap=cmap)
    smap.set_array(orders)
    if plot_mem:
        f, (ax1,ax2,ax3) = plt.subplots(3,1,sharex=True)
        f.set_figheight(f.get_figheight()*3.0/2.0)
        ax3.set_yscale('log')
    else:
        f, (ax1,ax2) = plt.subplots(2,1,sharex=True)
    ax1.set_yscale('log')
    # Loop over total_order
    for o in orders:
        kws['total_order'] = o
        kws['order1'] = None
        kws['plot'] = False
        kws['ngz'] = 0
        kws['extendtag'] = '_to{}'.format(o)
        vlist = range(0,kws['total_order'])
        result = vary_selection_stats('order2',vlist,verbose=True,**kws)
        Nvar = len(vlist)
        t = np.empty(Nvar, dtype='float')
        df = np.empty(Nvar, dtype='float')
        nf = np.empty(Nvar, dtype='float')
        cc = np.empty(Nvar, dtype='float')
        rc = np.empty(Nvar, dtype='float')
        ts = np.empty(Nvar, dtype='float')
        mem = np.empty(Nvar, dtype='float')
        for i,v in enumerate(vlist):
            t[i] = result[v]['tm']
            df[i] = result[v]['ndf']
            nf[i] = result[v]['nf']
            cc[i] = float(result[v]['cc'][0])/float(result[v]['cc'][1])
            rc[i] = float(result[v]['rc'][0])/float(result[v]['rc'][1])
            ts[i] = result[v].get('ts',0.0)
            mem[i] = result[v].get('mem',0.0)/(1024.0*1024.0)
        # Plot
        if len(orders) == 1:
            clr_f = 'k'
            clr_cc = 'b'
            clr_rc = 'r'
        else:
            clr_f = smap.to_rgba(o)
            clr_cc = smap.to_rgba(o)
            clr_rc = smap.to_rgba(o)
        ptkws = dict(linestyle='-',color=clr_f,label='Time')
        pfkws = dict(linestyle='-',color=clr_f)
        pcckws = dict(linestyle='-.',color=clr_cc)
        prckws = dict(linestyle=':',color=clr_rc)
        if o == orders[0]:
            pfkws['label'] = 'Files'
            pcckws['label'] = 'Coarse Coll.'
            prckws['label'] = 'Refined Coll.'
        if kws.get('plot_errors',False):
            ax1.errorbar(vlist,t,yerr=ts,**ptkws)
        else:
            ax1.plot(vlist,t,**ptkws)
        ax2.plot(vlist,df/nf,**pfkws)
        ax2.plot(vlist,cc,**pcckws)
        ax2.plot(vlist,rc,**prckws)
    # Formatting
    if plot_mem:
        ax3.plot(vlist,mem,linestyle='-',color='k')
        ax3.set_xlabel('Order of Refined Index', fontsize=14, fontweight='bold')
        ax3.set_ylabel('Size of Index (MB)', fontsize=14, fontweight='bold')
    else:
        ax2.set_xlabel('Order of Refined Index', fontsize=14, fontweight='bold')
    ax1.set_ylabel('Time (s)',fontsize=14, fontweight='bold')
    ax2.set_ylabel('% Files Identified/\n Cells with Collisions', fontsize=14, fontweight='bold')
    ax2.set_ylim((-0.1,1.1))
    for ax in [ax1,ax2]:
        for axis in ['top','bottom','left','right']:
            ax.spines[axis].set_linewidth(4)
        ax.tick_params(width=4)
    plt.sca(ax2)
    plt.legend(loc=3,bbox_to_anchor=(0., 1.02, 1., .102),
               ncol=3,mode="expand", borderaxespad=0.,
               frameon=False)
    if len(orders) > 1:
        cbar = f.colorbar(smap, ax1,#use_gridspec=True,
                          orientation='horizontal')
        cbar.set_label('Total Order of Combined Indices', 
                       fontsize=14, fontweight='bold')
    # Save
    plt.savefig(fname)
    print(fname)

def plot_vary_decomp(plot_collisions=False,plot_mem=False,**kws):
    import matplotlib as mpl
    import matplotlib.pyplot as plt
    mpl.rc('font', weight='bold')
    mpl.rc('lines',linewidth=5)
    mpl.rc('axes',linewidth=4)
    list_decomp = ['random','sliced','morton','hilbert']
    order2 = 0
    total_order = 7
    fname = 'vary_decomp_to{}.png'.format(order2)
    # Set up plot
    plt.close('all')
    cmap = plt.get_cmap('jet') 
    cnorm = mpl.colors.Normalize(vmin=0, vmax=len(list_decomp)-1)
    smap = mpl.cm.ScalarMappable(norm=cnorm, cmap=cmap)
    clrs = ['m','c','r','b']
    stys = [':','-.','--','-']
    if plot_mem:
        f, (ax1,ax2,ax3) = plt.subplots(3,1,sharex=True)
        f.set_figheight(f.get_figheight()*3.0/2.0)
        ax3.set_yscale('log')
    else:
        f, (ax1,ax2) = plt.subplots(2,1,sharex=True)
    ax1.set_yscale('log')
    # Loop over total_order
    for o,decomp in enumerate(list_decomp):
        kws['total_order'] = None
        kws['decomp'] = decomp
        kws['order2'] = order2
        kws['ngz'] = 0
        kws['plot'] = False
        kws['extendtag'] = '_or{}'.format(order2)
        vlist = range(1,total_order+1)
        result = vary_selection_stats('order1', vlist, verbose=True, **kws)
        Nvar = len(vlist)
        t = np.empty(Nvar, dtype='float')
        df = np.empty(Nvar, dtype='float')
        gf = np.empty(Nvar, dtype='float')
        nf = np.empty(Nvar, dtype='float')
        cc = np.empty(Nvar, dtype='float')
        mem = np.empty(Nvar, dtype='float')
        # rc = np.empty(Nvar, dtype='float')
        ts = np.empty(Nvar, dtype='float')
        for i,v in enumerate(vlist):
            t[i] = result[v]['tm']
            df[i] = result[v]['ndf']
            gf[i] = result[v]['ngf']
            nf[i] = result[v]['nf']
            cc[i] = float(result[v]['cc'][0])/float(result[v]['cc'][1])
            # rc[i] = float(result[v]['rc'][0])/float(result[v]['rc'][1])
            ts[i] = result[v].get('ts',0.0)
            mem[i] = result[v].get('mem',0.0)
        # Plot
        clr = clrs[o]
        sty = stys[o]
        #clr = smap.to_rgba(o)
        if plot_collisions:
            ptkws = dict(linestyle='-',color=clr,label='{} Time'.format(decomp.title()))
            pfkws = dict(linestyle='-',color=clr,label='{} Files'.format(decomp.title()))
            pckws = dict(linestyle='--',color=clr,label='{} Collisions'.format(decomp.title()))
            pmkws = dict(linestyle='-',color=clr,label='{} Memory'.format(decomp.title()))
        else:
            ptkws = dict(linestyle=sty,color=clr,label=decomp.title())
            pfkws = dict(linestyle=sty,color=clr,label=decomp.title())
            pmkws = dict(linestyle=sty,color=clr,label=decomp.title())
        if kws.get('plot_errors',False):
            ax1.errorbar(vlist,t,yerr=ts,**ptkws)
        else:
            ax1.plot(vlist,t,**ptkws)
        ax2.plot(vlist,df/nf,**pfkws)
        if plot_collisions:
            ax2.plot(vlist,cc,**pckws)
        if plot_mem:
            ax3.plot(vlist,mem,**pmkws)
    # Formatting
    if plot_mem:
        ax3.set_xlabel('Order of Index', fontsize=14, fontweight='bold')
        ax3.set_ylabel('Size of Index (MB)', fontsize=14, fontweight='bold')
    else:
        ax2.set_xlabel('Order of Index', fontsize=14, fontweight='bold')
    ax1.set_ylabel('Time (s)',fontsize=14, fontweight='bold')
    if plot_collisions:
        ax2.set_ylabel('% files/collisions', fontsize=14, fontweight='bold')
    else:
        ax2.set_ylabel('% Files Identified', fontsize=14, fontweight='bold')
    ax2.set_ylim((-0.1,1.1))
    for ax in [ax1,ax2]:
        ax.tick_params(width=4)
    plt.sca(ax2)
    plt.legend(loc=9,bbox_to_anchor=(0., 2.07, 1., .102),
               ncol=2,mode="expand", borderaxespad=0.,
               frameon=False)
    # Save
    plt.savefig(fname)
    print(fname)


def test_particle_regions():
    np.random.seed(int(0x4d3d3d3))
    dx = 0.1
    verbose = False
    # We are going to test having 31, 127, 128 and 257 data files.
    # for nfiles in [2, 31, 32, 33, 127, 128, 129]:
    #for nfiles in [2, 31, 32, 33]:
    for nfiles in [2, 31, 127, 128, 129]:
        if verbose: print("nfiles = {}".format(nfiles))
        # Now we create particles 
        # Note: we set order1 to log2(nfiles) here for testing purposes. 
        # Inside the code we set it to min(log2(nfiles), 8)?
        # langmm: this is not strictly true anymore
        N = nfiles
        order1 = int(np.ceil(np.log2(N))) # Ensures zero collisions
        order2 = 1 # No overlap for N = nfiles
        exact_division = (N == (1 << order1))
        div = float(nfiles)/float(1 << order1)
        reg, cc, rc, mem = FakeBitmap(nfiles**3, nfiles, order1, order2, decomp='grid',
                                      DLE=np.array([0.0, 0.0, 0.0]),
                                      DRE=np.array([nfiles, nfiles, nfiles]), 
                                      verbose=verbose)
        # Loop over regions selecting single files
        fr = FakeRegion(nfiles)
        for i in range(nfiles):
            fr.set_edges(i, dx)
            selector = RegionSelector(fr)
            df, gf = reg.identify_data_files(selector, ngz=1)
            if exact_division:
                yield assert_equal, len(df), 1, "selector {}, number of files".format(i)
                yield assert_equal, df[0], i, "selector {}, file selected".format(i)
                if i == 0:
                    yield assert_equal, len(gf), 1, "selector {}, number of ghost files".format(i)
                    yield assert_equal, gf[0], i+1, "selector {}, ghost files".format(i)
                elif i == (nfiles - 1):
                    yield assert_equal, len(gf), 1, "selector {}, number of ghost files".format(i)
                    yield assert_equal, gf[0], i-1, "selector {}, ghost files".format(i)
                else:
                    yield assert_equal, len(gf), 2, "selector {}, number of ghost files".format(i)
                    yield assert_equal, gf[0], i-1, "selector {}, ghost files".format(i)
                    yield assert_equal, gf[1], i+1, "selector {}, ghost files".format(i)
            else:
                lf_frac = np.floor(float(fr.left_edge[0])/div)*div
                rf_frac = np.floor(float(fr.right_edge[0])/div)*div
                # Selected files
                lf = int(np.floor(lf_frac) if ((lf_frac % 0.5) == 0) else np.round(lf_frac))
                rf = int(np.floor(rf_frac) if ((rf_frac % 0.5) == 0) else np.round(rf_frac))
                if (rf+0.5) >= (rf_frac+div): rf -= 1
                if (lf+0.5) <= (lf_frac-div): lf += 1
                df_ans = np.arange(max(lf,0),min(rf+1,nfiles))
                # print df, df_ans
                # print lf_frac, lf, rf_frac, rf, lf_frac-div, (rf_frac+div)
                yield assert_array_equal, df, df_ans, "selector {}, file array".format(i)
                # Ghost zones selected files
                lf_ghost = int(max(np.floor(lf_frac - div) if (((lf_frac-div) % 0.5) == 0) else np.round(lf_frac - div),0))
                rf_ghost = int(min(np.floor(rf_frac + div) if (((rf_frac+div) % 0.5) == 0) else np.round(rf_frac + div),nfiles-1))
                if (rf_ghost+0.5) >= (rf_frac+2*div): rf_ghost -= 1
                gf_ans = []
                if lf_ghost < lf: gf_ans.append(lf_ghost)
                if rf_ghost > rf: gf_ans.append(rf_ghost)
                gf_ans = np.array(gf_ans)
                yield assert_array_equal, gf, gf_ans, "selector {}, ghost file array".format(i)

        # print reg.masks.shape
        # for mask in reg.masks:
        #     print mask.shape
        #     maxs = np.unique(mask.max(axis=-1).max(axis=-1))
        #     mins = np.unique(mask.min(axis=-1).min(axis=-1))
        #     yield assert_equal, maxs, mins
        #     yield assert_equal, maxs, np.unique(mask)

def test_save_load_bitmap():
    verbose = False
    fname_fmt = "temp_bitmasks{}.dat"
    i = 0
    fname = fname_fmt.format(i)
    while os.path.isfile(fname):
        i += 1
        fname = fname_fmt.format(i)
    np.random.seed(int(0x4d3d3d3))
    nfiles = 32
    order1 = 2
    order2 = 2 # Maximum collisions
    pos = np.random.normal(0.5, scale=0.05, size=(NPART/nfiles,3)) * (DRE-DLE) + DLE
    pos[:,0] = (DW[0]/nfiles)/2
    for i in range(3):
        np.clip(pos[:,i], DLE[i], DRE[i], pos[:,i])
    reg0 = ParticleBitmap(DLE, DRE, nfiles,
                          index_order1 = order1,
                          index_order2 = order2)
    # Coarse index
    for i in range(nfiles):
        reg0._coarse_index_data_file(pos, i)
        pos[:,0] += (DW[0]/nfiles)
    pos[:,0] = (DW[0]/nfiles)/2
    cc = reg0.find_collisions_coarse(verbose=verbose)
    # Refined index
    max_npart = pos.shape[0]
    sub_mi1 = np.zeros(max_npart, "uint64")
    sub_mi2 = np.zeros(max_npart, "uint64")
    for i in range(0,nfiles):
        reg0._refined_index_data_file(pos, reg0.masks.sum(axis=1).astype('uint8'),
                                      sub_mi1, sub_mi2, i)
        pos[:,0] += (DW[0]/nfiles)
    pos[:,0] = (DW[0]/nfiles)/2
    rc = reg0.find_collisions_refined(verbose=verbose)
    # Owners
    reg0.set_owners()
    # Save
    reg0.save_bitmasks(fname)
    # Load
    reg1 = ParticleBitmap(DLE, DRE, nfiles,
                          index_order1 = order1,
                          index_order2 = order2)
    reg1.load_bitmasks(fname)
    # Check equality
    yield assert_true, reg0.iseq_bitmask(reg1)
    # Remove file
    os.remove(fname)

if __name__=="__main__":
    for i in test_add_particles_random():
        i[0](*i[1:])
    time.sleep(1)

os33 = "snapshot_033/snap_033.0.hdf5"
@requires_file(os33)
def test_get_smallest_dx():
    ds = yt.load(os33)
    yield assert_equal, ds.index.get_smallest_dx(), \
        ds.domain_width / (ds.domain_dimensions*2.**(ds.index.max_level))

# TODO: Change these!!!!
bc94 = "/mnt/gv0/mturk/big_cosmo/snapdir_094/snap_lcdma_1024_094.0"
bc94_coll = "/root/projects/bitmap/big_cosmo_bitmask_7_5_coll.dat"

@requires_file(bc94)
@requires_file(bc94_coll)
def test_initialize_index():
    order1 = 7
    order2 = 5
    ds = yt.GadgetDataset(bc94, long_ids = True)
    ds.index._initialize_index(order1=order1, order2=order2)
    reg1 = ds.index.regions
    reg0 = ParticleBitmap(ds.domain_left_edge, ds.domain_right_edge,
                          len(ds.index.data_files), ds.over_refine_factor,
                          ds.n_ref, index_order1=order1, index_order2=order2)
    reg0.load_bitmasks(fname=bc94_coll)
    yield assert_true, reg0.iseq_bitmask(reg1)

# To avoid loading
class FakeBC94DS:
    unit_registry = UnitRegistry()
    unit_registry.add('code_length', 1.0, dimensions.length)
    domain_left_edge = YTArray([0.0, 0.0, 0.0], "code_length",
                               registry=unit_registry)
    domain_right_edge = YTArray([135.54, 135.54, 135.54], "code_length",
                                registry=unit_registry)
    domain_width = YTArray([135.54, 135.54, 135.54], "code_length",
                           registry=unit_registry)
    domain_center = YTArray([ 67.77,  67.77,  67.77], "code_length",
                            registry=unit_registry)
    periodicity = (True, True, True)
    over_refine_factor = 1
    n_ref = 64
    nfiles = 512
    order1 = 7
    order2 = 5
    domain_dimensions = np.array(3*[nfiles], dtype='int32')
    default_fluid_type = 'gas'

# class FakeBC94SphericalRegion:
#     #from yt.geometry.selection_routines import SphereSelector
#     from yt.geometry.selection_routines import sphere_selector
#     def __init__(self, c, r):
#         self.ds = FakeBC94DS()
#         self.nfiles = self.ds.nfiles
#         self.center = self.ds.domain_center + c*self.ds.domain_width
#         self.radius = r*self.ds.domain_width[0]
#         #self.selector = SphereSelector(self)
#         self.selector = sphere_selector(self)

# TODO: remove dependence on bc94 (only use bitmask)
@requires_file(bc94)
@requires_file(bc94_coll)
def test_fill_masks():
    from yt.utilities.lib.ewah_bool_wrap import BoolArrayCollection
    from yt.geometry.particle_oct_container import ParticleBitmapSelector
    from yt.data_objects.selection_data_containers import YTSphere
    ds_empty = FakeBC94DS()

    order1 = 7
    order2 = 5
    ngz = 1
    ds = yt.GadgetDataset(bc94, long_ids = True)
    ds.index._initialize_index(fname=bc94_coll, order1=order1, order2=order2)
    print "default_fluid_type",getattr(ds,"default_fluid_type")
    reg = ds.index.regions
    reg0 = ParticleBitmap(ds_empty.domain_left_edge, ds_empty.domain_right_edge,
                          ds_empty.nfiles, ds_empty.over_refine_factor,
                          ds_empty.n_ref, index_order1=ds_empty.order1, index_order2=ds_empty.order2)
    reg0.load_bitmasks(fname=bc94_coll)
    tests_sph = {(0,0.9/float(1 << order1)):  8,
                 (0,1.0/float(1 << order1)): 20,
                 (0,1.1/float(1 << order1)): 32,
                 (0.5/float(1 << order1),0.49/float(1 << order1)): 1,
                 (0.5/float(1 << order1),0.50/float(1 << order1)): 4,
                 (0.5/float(1 << order1),0.51/float(1 << order1)): 7,
                 (0,1.9/float(1 << order1)): 64,
                 # (0,2.0/float(1 << order1)): 76, # floating point equality...
                 (0,2.1/float(1 << order1)): 88,
                 (0.5/float(1 << order1),1.49/float(1 << order1)): 27,
                 (0.5/float(1 << order1),1.50/float(1 << order1)): 30,
                 (0.5/float(1 << order1),1.51/float(1 << order1)): 33}
    for (c, r), nc_s in tests_sph.items():
        mm_s = BoolArrayCollection()
        mm_g = BoolArrayCollection()
        mm_s0 = BoolArrayCollection()
        mm_g0 = BoolArrayCollection()
        center = ds.domain_center + c*ds.domain_width
        radius = r*ds.domain_width[0]
        sp = ds.sphere(center, radius)
        #sp0 = FakeBC94SphericalRegion(c, r)
        sp0 = YTSphere(center, radius, ds=ds_empty)
        ms = ParticleBitmapSelector(sp.selector, reg, ngz=ngz)
        ms0 = ParticleBitmapSelector(sp0.selector, reg0, ngz=ngz)
        ms.fill_masks(mm_s, mm_g)
        ms0.fill_masks(mm_s0, mm_g0)
        yield assert_equal, mm_s.count_coarse(), nc_s
        print(c,r,nc_s,mm_s.count_coarse(),"succeeded")
        yield assert_equal, mm_s0.count_coarse(), nc_s
        print(c,r,nc_s,mm_s0.count_coarse(),"succeeded")
