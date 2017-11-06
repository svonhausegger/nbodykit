from .base import PairCountBase, verify_input_sources
import numpy
import logging
from six import string_types

class SimulationBoxPairCount(PairCountBase):
    r"""
    Count (weighted) pairs of objects in a simulation box using the
    :mod:`Corrfunc` package and a 1D, 2D, projected, or angular coordinate basis.

    See the Notes below for the allowed coordinate dimensions

    Results are computed when the object is inititalized. See the documenation
    of :func:`~SimulationBoxPairCount.run` for the attributes storing the
    results.

    .. note::

        The algorithm expects the positions of particles in a simulation box to
        be the Cartesian ``x``, ``y``, and ``z`` vectors. To compute
        pair counts on survey data, using right ascension, declination, and
        redshift, see :class:`~nbodykit.algorithms.SurveyDataPairCount`.

    Parameters
    ----------
    mode : '1d', '2d', 'projected', 'angular'
        compute pair counts as a function of the specified coordinate basis;
        see the Notes section below for specifics
    first : CatalogSource
        the first source of particles, providing the 'Position' column
    edges : array_like
        the separation bin edges along the first coordinate dimension;
        depending on ``mode``, the options are :math:`r`, :math:`r_p`, or
        :math:`\theta`. Expected units for distances are :math:`\mathrm{Mpc}/h`
        and degrees for angles. Length of nbins+1
    BoxSize : float, 3-vector, optional
        the size of the box; if 'BoxSize' is not provided in the source
        'attrs', it must be provided here
    periodic : bool, optional
        whether to use periodic boundary conditions
    second : CatalogSource, optional
        the second source of particles to cross-correlate
    los : {'x', 'y', 'z'}, int, optional
        the axis of the simulation box to treat as the line-of-sight direction;
        this can be provided as string identifying one of 'x', 'y', 'z' or
        the equivalent integer number of the axis
    Nmu : int, optional
        the number of :math:`\mu` bins, ranging from 0 to 1; requred if
        ``mode='2d'``
    pimax : float, optional
        The maximum separation along the line-of-sight when ``mode='projected'``.
        Distances along the :math:`\pi` direction are binned with unit
        depth. For instance, if ``pimax=40``, then 40 bins will be created
        along the :math:`\pi` direction.
    weight : str, optional
        the name of the column in the source specifying the particle weights
    show_progress : bool, optional
        if ``True``, perform the pair counting calculation in 10 iterations,
        logging the progress after each iteration; this is useful for
        understanding the scaling of the code
    **config : key/value pairs
        additional keywords to pass to the :mod:`Corrfunc` function

    Notes
    -----
    This class can compute pair counts using several different coordinate
    choices, based on the value of the input argument ``mode``. The choices
    are:

    * ``mode='1d'`` : compute pairs as a function of the 3D separation :math:`r`
    * ``mode='2d'`` : compute pairs as a function of the 3D separation :math:`r`
      and the cosine of the angle to the line-of-sight, :math:`\mu`
    * ``mode='projected'`` : compute pairs as a function of distance perpendicular
      and parallel to the line-of-sight, :math:`r_p` and :math:`\pi`
    * ``mode='angular'`` : compute pairs as a function of angle on the sky, :math:`\theta`

    Note that for angular pair counts, the observer is placed at the center of the
    box.
    """
    logger = logging.getLogger('SimulationBoxPairCount')

    def __init__(self, mode, first, edges, BoxSize=None, periodic=True,
                    second=None, los='z', Nmu=None, pimax=None,
                    weight='Weight', show_progress=False, **config):

        # check input 'los'
        if isinstance(los, string_types):
            assert los in 'xyz', "``los`` should be one of 'x', 'y', 'z'"
            los = 'xyz'.index(los)
        elif isinstance(los, int):
            if los < 0: los += 3
            assert los in [0,1,2], "``los`` should be one of 0, 1, 2"
        else:
            raise ValueError("``los`` should be either ['x', 'y', 'z'] or [0,1,2]")

        # verify the input sources
        BoxSize = verify_input_sources(first, second, BoxSize, ['Position', weight])

        # init the base class (this verifies input arguments)
        PairCountBase.__init__(self, mode, edges, first, second, Nmu, pimax, show_progress)

        # save the rest of the meta-data
        self.attrs['BoxSize'] = BoxSize
        self.attrs['periodic'] = periodic
        self.attrs['weight'] = weight
        self.attrs['config'] = config
        self.attrs['los'] = los

        # test maximum separation and periodic boundary conditions
        if periodic and mode != 'angular':
            min_box_side = 0.5*self.attrs['BoxSize'].min()
            if numpy.amax(edges) > min_box_side or mode == 'projected' and pimax > min_box_side:
                raise ValueError(("periodic pair counts cannot be computed for Rmax > BoxSize/2"))

        # run the algorithm
        self.run()

    def run(self):
        """
        Calculate the pair counts in a simulation box.
        This adds the following attributes to the class:

        - :attr:`SimulationBoxPairCount.pairs`

        Attributes
        ----------
        pairs : :class:`~nbodykit.binned_statistic.BinnedStatistic`
            a BinnedStatistic object holding the pair count results.
            The coordinate grid will be ``(r,)``, ``(r,mu)``, ``(rp, pi)``,
            or ``(theta,)`` when ``mode`` is '1d', '2d', 'projected', 'angular',
            respectively.

            The BinnedStatistic stores the following variables:

            - ``r``, ``rp``, or ``theta`` : the mean separation value in the bin
            - ``npairs``: the number of pairs in the bin
            - ``weightavg``: the average weight value in the bin; each pair
              contributes the product of the individual weight values
        """
        # setup
        mode = self.attrs['mode']
        first, second = self.first, self.second
        attrs = self.attrs.copy()

        # determine the axes order
        # NOTE: LOS axis should be final column
        los = attrs['los']
        axes_order = [i for i in [0,1,2] if i != los] + [los]

        # compute the max cartesian distance for smoothing
        smoothing = numpy.max(attrs['edges'])
        if mode == 'projected':
            smoothing = numpy.sqrt(smoothing**2 + attrs['pimax']**2)
        elif mode == 'angular':
            smoothing = 2 * numpy.sin(0.5 * numpy.deg2rad(smoothing))

        if mode != 'angular':
            from .domain import decompose_box_data

            # domain decompose the data
            (pos1, w1), (pos2, w2) = decompose_box_data(first, second, attrs,
                                                        self.logger, smoothing)

            # reorder to make LOS last column
            pos1 = pos1[:,axes_order]
            pos2 = pos2[:,axes_order]
        else:

            from nbodykit.transform import CartesianToEquatorial
            from .domain import decompose_survey_data

            # go from (x,y,z) to (ra,dec), using observer in the middle of the box
            BoxCenter = 0.5*attrs['BoxSize']
            first['ra'], first['dec'] = CartesianToEquatorial(first['Position'], BoxCenter)
            if second is not None and second is not first:
                second['ra'], second['dec'] = CartesianToEquatorial(second['Position'], BoxCenter)

            # domain decompose the data
            attrs['ra'], attrs['dec'] = 'ra', 'dec'
            (pos1, w1), (pos2, w2) = decompose_survey_data(first, second, attrs,
                                                            self.logger, smoothing, angular=True)

        # get the Corrfunc callable based on mode
        kws = {k:attrs[k] for k in ['periodic', 'BoxSize', 'show_progress']}
        if attrs['mode'] == '1d':
            from .corrfunc.theory import DD
            func = DD(attrs['edges'], **kws)

        elif attrs['mode'] == '2d':
            from .corrfunc.theory import DDsmu
            func = DDsmu(attrs['edges'], attrs['Nmu'], **kws)

        elif attrs['mode'] == 'projected':
            from .corrfunc.theory import DDrppi
            func = DDrppi(attrs['edges'], attrs['pimax'], **kws)

        elif attrs['mode'] == 'angular':
            from .corrfunc.mocks import DDtheta_mocks
            func = DDtheta_mocks(attrs['edges'], show_progress=attrs['show_progress'])

        # do the calculation
        self.pairs = func(pos1, w1, pos2, w2, **attrs['config'])
