from types import FunctionType
import warnings

import numpy as np

try:
    from astropy.units import Quantity, rad
except ImportError:
    class Quantity(object):
        pass

    class rad(object):
        pass


def _build_table(data):
    """Turn a list of dicts into an astropy table
    if possible, or a numpy structured array otherwise"""
    try:
       return _data_to_astropy(data)
    except ImportError:
        return _data_to_numpy(data)

def _data_to_astropy(data):
    """Convert a list of dicts to an astropy table"""
    from astropy.table import Table, Column
    names = sorted(data[0].keys())
    cols = [ [d[c] for d in data] for c in names]
    return Table(cols, names=names)

def _data_to_numpy(data):
    """Convert a list of dicts to an numpy structured array"""
    names = sorted(data[0].keys())
    data = [tuple(d[c] for c in names) for d in data]
    dtypes = [(n, np.dtype(d)) for n, d in zip(names, data[0])]
    return np.array(data, dtype=dtypes)

def _qsplit(q):
    """Split a potential astropy Quantity into unit/quantity"""
    if isinstance(1 * q, Quantity):
        return q.unit, q.value

    return 1, q


class ScalarStatistic(object):
    #This class does all of the heavy computation
    def __init__(self, values, indices):
        """
        Compute pixel-level statistics from
        a scalar field, sampled at specific locations

        Parameters
        ----------
        values : 1D ndarray
                data values to use
        indices: tuple of 1D arrays
                 Location of each element of values.
                 The ith array in the tuple describes the
                 ith positional dimension
        """
        self.values = values.astype(np.float)
        self.indices = indices

    def mom0(self):
        """The sum of the values"""
        return np.nansum(self.values)

    def mom1(self):
        """The intensity-weighted mean position"""
        m0 = self.mom0()
        return [np.nansum(i * self.values) / m0
                for i in self.indices]

    def mom2(self):
        """The intensity-weighted covariance matrix"""
        mom1 = self.mom1()
        mom0 = self.mom0()
        v = self.values / mom0

        nd = len(self.indices)
        zyx = tuple(i - m for i, m in zip(self.indices, mom1))

        result = np.zeros((nd, nd))

        for i in range(nd):
            result[i, i] = np.nansum(v * zyx[i] ** 2)
            for j in range(i + 1, nd):
                result[i, j] = result[j, i] = np.nansum(v * zyx[i] * zyx[j])

        return result

    def mom2_along(self, direction):
        """Intensity-weighted variance/covariance along 1 or more directions

        Parameters
        ----------
        direction : array like
                  One or more set of direction vectors. Need not be normalized

        Returns
        -------
        The variance (or co-variance matrix) of the data along
        the specified direction(s).
        """
        w = np.atleast_2d(direction).astype(np.float)
        for row in w:
            row /= np.linalg.norm(row)
        result = np.dot(np.dot(w, self.mom2()), w.T)
        if result.size == 1:
            result = np.asscalar(result)
        return result

    def paxes(self):
        """
        The principal axes of the data (direction of greatest elongation)

        Returns
        -------
        Ordered list of ndarrays

        Each array is a normalized direction vector. The arrays
        are sorted in decreasing order of elongation of the data
        """
        mom2 = self.mom2()
        w, v = np.linalg.eig(mom2)
        order = np.argsort(w)

        return tuple(v[:, o] for o in order[::-1])

    def projected_paxes(self, axes):
        """
        The principal axes of a projection of the data onto a subspace

        Paramters
        ---------
        axes : array-like, (nnew, nold)
               The projection to take. Each row defines a unit vector in
               the new coordinate system

        Returns
        --------
        tuple of arrays (nnew items)

        The ordered principal axes in the new space
        """
        mom2 = self.mom2_along(axes)
        w, v = np.linalg.eig(mom2)
        order = np.argsort(w)

        return tuple(v[:, o] for o in order[::-1])

    def count(self):
        """Number of elements in the dataset"""
        return self.values.size

    def surface_area(self):
        raise NotImplementedError

    def perimeter(self, plane=None):
        raise NotImplementedError


class VectorStatistic(object):
    def __init__(self, values_tuple, indices):
        raise NotImplementedError

    def divergence(self):
        raise NotImplementedError

    def curl(self):
        raise NotImplementedError


class MetaData(object):
    """A descriptor to wrap around metadata dictionaries

    Let's classes reference self.x instead of self.metadata['x'],
    """
    def __init__(self, key, description, default=1, strict=False):
        """
        Parameters
        ----------
        key : str
               Metadata name.
        description : str
               What the quantity describes
        default : scalar
               Default value if metadata not provided
        strict : bool
               If True, raise KeyError if metadata not provided.
               This overrides default
        """
        self.key = key
        self.description = description or 'no description'
        self.default = default
        self.strict = strict

    def __get__(self, instance, type=None):
        if instance is None:
            return self

        if self.strict and self.key not in instance.metadata:
            raise KeyError("Required metadata item not found: %s" % self)
        return instance.metadata.get(self.key, self.default)

    def __str__(self):
        return "%s (%s)" % (self.key, self.description)


def _missing_metadata(cl, md):
    """Find missing metadata entries in a metadata dict

    Paramters
    ---------
    cls : Class with MetaData descriptors
    md : metadata dictionary
    """
    result = []
    return [m for m in cl.__dict__.values() if isinstance(m, MetaData)
            and m.key not in md]

def _warn_missing_metadata(cl, md):
    missing = _missing_metadata(cl, md)
    if len(missing) == 0:
        return

    required = [m for m in missing if m.strict]
    if len(required):
        raise RuntimeError(
            "The following missing metadata items are required:\n\t" +
            "\n\t".join(str(m) for m in required))

    for m in missing:
        warnings.warn("Missing Metadata:\n\t %s\n\t Defaulting to %s=%s" %
                      (m, m.key, m.default))


class PPVStatistic(object):
    dx = MetaData('dx', 'Angular length of a pixel')
    dv = MetaData('dv', 'Velocity channel width')
    vaxis = MetaData('vaxis', 'Index of velocity axis (numpy convention)')
    bmaj = MetaData('bmaj', 'Beam major axis, sigma', default=0)
    bmin = MetaData('bmin', 'Beam minor axis, sigma', default=0)
    bunit = MetaData('bunit', 'Unit of intensity')
    dist = MetaData('dist', 'Distance')

    def __init__(self, stat, metadata):
        """
        Compute properties of structures in a PPV cube

        Parameters
        ----------
        stat :   ScalarStatistic instance
        metadata : dict
                 Key-value paris of metadata
        """
        self.stat = stat
        self.metadata = metadata

    def flux(self):
        """Integrated flux

        sum(v_i * dx^2 * dv)
        """
        fac = self.bunit * self.dx ** 2 * self.dv
        return fac * self.stat.mom0()

    def luminosity(self):
        """Integrated luminosity

        sum(v_i * dx_linear^2 * dv)
        """
        #disambiguate between degree/radian dx
        #if astropy unit is used
        try:
            fac = (1 * self.dx).unit.to(rad)
            fac /= (1 * self.dx).unit
        except AttributeError:
            # metadata not a quantity. Assuming dx=degrees
            fac = np.radians(1)
        return self.dist ** 2 * self.flux() * fac ** 2

    def _sky_paxes(self):
        vaxis = self.vaxis
        ax = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        ax.pop(vaxis)
        a, b = self.stat.projected_paxes(ax)
        a = list(a)
        a.insert(0, vaxis)
        b = list(b)
        b.insert(0, vaxis)
        return a, b

    def sky_maj(self):
        """Major axis of the projection onto the PP plane

        Intensity weighted second moment in direction
        of greatest elongation in the PP plane
        """
        dx = self.dx
        a, b = self._sky_paxes()
        return dx * np.sqrt(self.stat.mom2_along(a))

    def sky_min(self):
        """Minor axis of the projection onto the PP plane

        Intensity-weighted second moment perpendicular
        to major axis, in PP plane
        """
        dx = self.dx
        a, b = self._sky_paxes()
        return dx * np.sqrt(self.stat.mom2_along(b))

    def sky_rad(self):
        """ Geometric mean of sky_maj and sky_min """
        u, a = _qsplit(self.sky_maj())
        u, b = _qsplit(self.sky_min())
        return u * np.sqrt(a * b)

    def vrms(self):
        """Intensity-weighted second moment of velocity"""
        ax = [0, 0, 0]
        ax[self.vaxis] = 1
        return self.dv * np.sqrt(self.stat.mom2_along(ax))

    def sky_deconvolved_rad(self):
        """sky_rad corrected for beam-smearing"""
        beam = self.bmaj * self.bmin
        u, a = _qsplit(self.sky_maj())
        u, b = _qsplit(self.sky_min())
        return u * np.sqrt(np.sqrt(a**2 - beam) * np.sqrt(b **2 - beam))

    def sky_pa(self):
        """The position angle of sky_maj, sky_min

        Returns the angle in degrees counter-clockwise from the +x axis
        """
        a, b = self._sky_paxes()
        a.pop(self.vaxis)
        return np.degrees(np.arctan2(a[0], a[1]))


class PPPStatistics(object):

    def __init__(self, rhostat, vstat, metadata):
        """
        Derive proeprties from PPP density and velocity fields

        This is not currently implemented

        Parameters
        ----------
        rhostat : ScalarStatistic instance
        vstat : VectorStatistic instance
        """
        raise NotImplementedError()

    def mass(self):
        pass

    def volume(self):
        pass

    def surface_area(self):
        pass

    def virial(self):
        pass

    def vrms(self):
        pass

    def vz_rms(self):
        pass

    def pressure_vz(self):
        pass

    def pressure(self):
        pass


def ppv_catalog(structures, metadata, fields=None):
    """
    Iterate over a collection of PPV structures,
    extracting several quantities from each, and building
    a catalog

    Parameters
    ----------
    structures : Iterable of Structures
         The structures to catalog (e.g., a dendrogram)

    metadata : dict of metadata
    fields : list of strings, optional
             The quantities to extract. If not provided,
             defaults to all PPV statistics
    """
    _warn_missing_metadata(PPVStatistic, metadata)

    result = []
    fields = fields or [k for k, v in PPVStatistic.__dict__.items()
                        if isinstance(v, FunctionType) and
                        not k.startswith('_')]
    print fields

    for struct in structures:
        stat = ScalarStatistic(struct.values, struct.indices)
        stat = PPVStatistic(stat, metadata)
        result.append(dict((lbl, getattr(stat, lbl)())
                           for lbl in fields))

    return _build_table(result)