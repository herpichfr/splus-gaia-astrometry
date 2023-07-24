# This module is meant to calculate the differences between the astrometry from S-PLUS to that
# of Gaia DR2 or DR3
# 2022-01-08: Expanding to compare any given photometric catalogue with Gaia
# Herpich F. R. 2022-12-20 fabiorafaelh@gmail.com
# GitHub: herpichfr
# ORCID: 0000-0001-7907-7884
# ---
# 2023-01-12: Adding multiprocessing to speed up the process
# 2023-01-13: Adding a function to calculate the astrometric differences between any SPLUS catalogue as long as the
# columns are properly named
# ---
# 2023-07-04: Changing parameters to run MAR columns
#

from statspack.statspack import contour_pdf
import os
import sys
import numpy as np
from astropy.io import ascii, fits
from astropy.coordinates import SkyCoord
import astropy.units as u
from astropy.coordinates import Angle
from astroquery.vizier import Vizier
import pandas as pd
import matplotlib.pyplot as plt
import multiprocessing
import time
import glob
import argparse
import logging
import colorlog


def parser():
    """
    Parse the arguments from the command line
    """

    parser = argparse.ArgumentParser(
        description='Calculate astrometric differences between S-PLUS and Gaia DR2 or DR3')
    parser.add_argument('-t', '--tiles', type=str,
                        help='List of tiles to be processed. Default is tiles_new_status.csv',
                        required=True)
    parser.add_argument('-f', '--footprint', type=str,
                        help='Fooprint file containing the positions of the S-PLUS tiles.',
                        required=True)
    parser.add_argument('-w', '--workdir', type=str, default=os.getcwd(),
                        help='Workdir path. Default is current directory',
                        required=False)
    parser.add_argument('-d', '--datadir', type=str, default=None,
                        help='Data directory path. Default is workdir',
                        required=False)
    # Gaia DR2 =345; Gaia DR3 = 355
    parser.add_argument('-g', '--gaia_dr', type=str, default='355',
                        help='Gaia catalogue number as registered at Vizier. Default is 355 (Gaia DR3)')
    parser.add_argument('-p', '--cat_name_preffix', type=str, default='',
                        help='Preffix of the catalogue name. Default is empty')
    parser.add_argument('-s', '--cat_name_suffix', type=str, default='',
                        help='Suffix of the catalogue name. Default is empty')
    parser.add_argument('-c', '--hdu', type=int, default=1,
                        help='HDU number of the catalogue when catalgue is FIST. Default is 1')
    parser.add_argument('-ra', '--racolumn', type=str, default='RA',
                        help='Column name of the RA in the catalogue. Default is RA')
    parser.add_argument('-de', '--deccolumn', type=str, default='DEC',
                        help='Column name of the DEC in the catalogue. Default is DEC')
    parser.add_argument('-m', '--mag_column', type=str, default='MAG_AUTO',
                        help='Column name of the magnitude in the catalogue. Default is MAG_AUTO')
    parser.add_argument('-fl', '--flags_column', type=str, default=None,
                        help='Column name of the flags in the catalogue. Default is None')
    parser.add_argument('-cs', '--clstar_column', type=str, default=None,
                        help='Column name of the clstar in the catalogue. Default is None')
    parser.add_argument('-fwhm', '--fwhm_column', type=str, default=None,
                        help='Column name of the fwhm in the catalogue. Default is None')
    parser.add_argument('-sn', '--sn_column', type=str, default=None,
                        help='Column name of the sn in the catalogue. Default is None')
    parser.add_argument('-ft', '--filetype', type=str, default='.fits',
                        help='Filetype of the catalogue. Default is .fits')
    parser.add_argument('-a', '--angle', type=float, default=1.0,
                        help='Angle to be used in the crossmatch. Default is 1.0')
    parser.add_argument('-sl', '--sn_limit', type=float, default=10.0,
                        help='Signal-to-noise limit to be used in the crossmatch. Default is 10.0')
    parser.add_argument('-o', '--output', type=str, default='splus_gaia_astrometry',
                        help='Output name. Default is splus_gaia_astrometry')
    parser.add_argument('-sf', '--savefig', action='store_true',
                        help='Save the figure. Default is False')
    parser.add_argument('-b', '--bins', type=int, default=1000,
                        help='Number of bins in the histogram. Default is 1000')
    parser.add_argument('-l', '--limit', type=float, default=0.5,
                        help='Limit of the histogram. Default is 0.5')
    parser.add_argument('--debug', action='store_true',
                        help='Prints out the debug of the code. Default is False')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Prints out the progress of the code. Default is False')

    if len(sys.argv) == 1:
        parser.print_help()
        raise argparse.ArgumentTypeError(
            'No arguments provided. Showing the help message.')

    args = parser.parse_args()

    return args


class SplusGaiaAst(object):

    def __init__(self, args):
        self.tiles: str = args.tiles
        self.workdir: str = args.workdir
        self.datadir: str = args.datadir
        self.gaia_dr = args.gaia_dr
        self.cat_name_preffix: str = args.cat_name_preffix
        self.cat_name_suffix: str = args.cat_name_suffix
        self.cathdu: int = args.hdu
        self.racolumn: str = args.racolumn
        self.decolumn: str = args.deccolumn
        self.mag_column: str = args.mag_column
        self.flags_column = args.flags_column
        self.clstar_column = args.clstar_column
        self.fwhm_column = args.fwhm_column
        self.sn_column = args.sn_column
        self.filetype = args.filetype
        self.angle: float = args.angle
        self.sn_limit: float = args.sn_limit
        self.output: str = args.output
        self.savefig: bool = args.savefig
        self.bins = args.bins
        self.limit = args.limit
        self.debug: bool = args.debug
        self.verbose: bool = args.verbose
        self.logger = logging.getLogger(__name__)

    def get_gaia(self, tile_coords, tilename, workdir=None, gaia_dr=None, angle=1.0):
        """
        Query Gaia photometry available at Vizier around a given centre.

        Parameters
        ----------
        tile_coords : SkyCoord object
          Central coordinates of the catalogue

        tilename : string
          Name of the central tile to use to search for the individual catalogues

        workdir : string
          Workdir path. Default is None

        gaia_dr : str | float
          Gaia's catalogue number as registered at Vizier

        angle : float
          Radius to search around the central coordinates through Gaia's catalogue in Vizier

        Returns
        -------
        gaia : Pandas DataFrame
            DataFrame containing the data queried around the given coordinates
        """
        workdir = self.workdir if workdir is None else workdir
        gaia_dr = self.gaia_dr if gaia_dr is None else gaia_dr
        angle = self.angle if angle is None else angle

        # query Vizier for Gaia's catalogue using gaia_dr number. gaia_dr number needs to be known beforehand
        self.logger.info('Querying gaia/vizier')
        v = Vizier(columns=['*', 'RAJ2000', 'DEJ2000'],
                   catalog='I/' + str(gaia_dr))
        v.ROW_LIMIT = 999999999
        # change cache location to workdir path to avoid $HOME overfill
        cache_path = os.path.join(workdir, '.astropy/cache/astroquery/Vizier/')
        if not os.path.isdir(cache_path):
            try:
                os.makedirs(cache_path, exist_ok=True)
            except FileExistsError:
                self.logger.info(
                    "File %s already exists. Skipping", cache_path)
        v.cache_location = cache_path
        gaia_data = v.query_region(tile_coords, radius=Angle(angle, "deg"))[0]
        # mask all nan objects in the coordinates columns before saving the catalogue
        mask = gaia_data['RAJ2000'].mask & gaia_data['DEJ2000'].mask
        gaia_data = gaia_data[~mask]
        logger.info('Gaia_data is %s', gaia_data)

        # save Gaia's catalogue to workdir
        gaia_cat_path = os.path.join(workdir, "".join(
            ['gaia_', gaia_dr, '_', tilename, '.csv']))
        if not os.path.isdir(os.path.join(workdir, "".join(['gaia_', gaia_dr]))):
            try:
                os.mkdir(os.path.join(workdir, "".join(['gaia_', gaia_dr])))
            except FileExistsError:
                self.logger.info("File %s already exists. Skipping",
                                 os.path.join(workdir, "".join(['gaia_', gaia_dr])))

        if self.verbose:
            self.logger.info(
                'Saving gaia catalogue to cache %s', gaia_cat_path)
        gaia_data.to_pandas().to_csv(gaia_cat_path, index=False)

        return gaia_data

    def calculate_astdiff(self, footprint, field_names, workdir=None, gaia_dr=None, cat_name_preffix=None,
                          cat_name_suffix=None):
        """
        Calculate the astrometric differences between any SPLUS catalogue as
        long as the columns are properly named

        Parameters
        ----------
        fields : list
          List of the tiles to be processed

        footprint : astropy Table
            Table containing the footprint of the survey

        workdir : string
            Workdir path. Default is None

        gaia_dr : str | float
            Gaia's catalogue number as registered at Vizier

        cat_name_preffix : str
            Preffix to be added to the name of the catalogue. Default is None

        cat_name_suffix : str
            Suffix to be added to the name of the catalogue. Default is None

        Returns
        -------
        astrometry : astropy Table
            Table containing the astrometric differences between the SPLUS catalogues and Gaia
        """

        gaia_dr = self.gaia_dr if gaia_dr is None else gaia_dr
        workdir = self.workdir if workdir is None else workdir
        cat_name_preffix = self.cat_name_preffix if cat_name_preffix is None else cat_name_preffix
        cat_name_suffix = self.cat_name_suffix if cat_name_suffix is None else cat_name_suffix

        results_dir = os.path.join(workdir, 'results/')
        if not os.path.isdir(results_dir):
            os.mkdir(results_dir)

        for tile in fields:
            if tile == 'fakename':
                self.logger.info('This is a filler name')
            else:
                path_to_results = os.path.join(
                    results_dir, "".join([tile, '_mar-gaiaDR3_diff.csv']))
                if os.path.isfile(path_to_results):
                    self.logger.info(
                        'Catalogue for tile %s already exists. Skipping' % tile)
                else:
                    sra = footprint['RA'][field_names == tile]
                    sdec = footprint['DEC'][field_names == tile]
                    tile_coords = SkyCoord(ra=sra[0], dec=sdec[0], unit=(
                        u.hour, u.deg), frame='icrs', equinox='J2000')

                    gaia_cat_path = os.path.join(workdir, "".join(
                        ['gaia_', gaia_dr, '/', tile, '.csv']))
                    if os.path.isfile(gaia_cat_path):
                        self.logger.info('Reading gaia cat from database')
                        gaia_data = ascii.read(gaia_cat_path, format='csv')
                    else:
                        gaia_data = self.get_gaia(tile_coords, tile)

                    if self.filetype == '.fits':
                        try:
                            scat = fits.open(os.path.join(
                                workdir, "".join([cat_name_preffix, tile, cat_name_suffix])))[self.cathdu].data
                        except TypeError:
                            self.logger.error(
                                'Catalogue is not in FITS format. Define the proper format of the default variable filetype')
                            raise TypeError('Catalogue is not in FITS format')
                    elif self.filetype == '.csv':
                        try:
                            scat = pd.read_csv(os.path.join(workdir,
                                               "".join([cat_name_preffix, tile, cat_name_suffix])))
                        except TypeError:
                            self.logger.error(
                                'Catalogue is not in CSV format. Define the proper format of the default variable filetype')
                            raise TypeError('Catalogue is not in CSV format')
                    else:
                        self.logger.error(
                            'Filetype for input catalogue not supported. Use FITS or CSV')
                        raise TypeError(
                            'Filetype for input catalogue not supported')

                    splus_coords = SkyCoord(
                        ra=scat[self.racolumn], dec=scat[self.decolumn], unit=(u.deg, u.deg))
                    gaia_coords = SkyCoord(
                        ra=gaia_data['RAJ2000'], dec=gaia_data['DEJ2000'], unit=(u.deg, u.deg))
                    idx, d2d, d3d = splus_coords.match_to_catalog_3d(
                        gaia_coords)
                    separation = d2d < 5.0 * u.arcsec

                    sample = (scat[self.mag_column] > 14.)
                    sample &= (scat[self.mag_column] < 19.)
                    if self.flags_column is None:
                        self.logger.warning(
                            'FLAGS column not available. Skipping using flags to object selection')
                    else:
                        sample &= scat[self.flags_column] == 0
                    if self.clstar_column is None:
                        self.logger.warning(
                            'CLASS_STAR column not available. Skipping using CLASS_STAR to object selection')
                    else:
                        try:
                            sample &= scat[self.clstar_column] > 0.95
                        finally:
                            self.logger.warning(
                                'Column for CLASS_STAR not found. Ignoring')
                    if self.fwhm_column is None:
                        self.logger.warning(
                            'FWHM column not available. Skipping using FWHM to object selection')
                    else:
                        sample &= scat[self.fwhm_column] * 3600 < 2.5
                    if self.sn_column is None:
                        self.logger.warning(
                            'SN column not available. Skipping using SN to object selection')
                    else:
                        sample &= scat[self.sn_column] > self.sn_limit

                    finalscat = scat[separation & sample]
                    finalgaia = gaia_data[idx][separation & sample]

                    abspm = abs(finalgaia['pmRA']) + abs(finalgaia['pmDE'])
                    # get masked values in gaia
                    mx = np.ma.masked_invalid(abspm)
                    lmt = np.percentile(abspm[~mx.mask], 95)
                    mask = (abspm < lmt) & ~mx.mask
                    dediff = 3600. * \
                        (finalscat[self.decolumn][mask]*u.deg -
                         np.array(finalgaia['DEJ2000'])[mask]*u.deg)
                    # calculate splus - gaia ra
                    radiff = 3600 * (finalscat[self.racolumn][mask] - finalgaia['RAJ2000'][mask]) *\
                        np.cos(np.array(finalgaia['DEJ2000'])[mask] * u.deg)

                    d = {'RA': finalscat[self.racolumn][mask],
                         'DEC': finalscat[self.decolumn][mask],
                         'RAJ2000': finalgaia['RAJ2000'][mask],
                         'DEJ2000': finalgaia['DEJ2000'][mask],
                         'radiff': radiff,
                         'dediff': dediff,
                         'abspm': abspm[mask]}
                    results = pd.DataFrame(data=d)
                    self.logger.info('saving results to %s' % path_to_results)
                    print('saving results to', path_to_results)
                    results.to_csv(path_to_results, index=False)

        return


def plot_diffs(datatab, contour=False, colours=None, savefig=False):
    """
    Plots the differences between S-PLUS and Gaia.

    Parameters
    ----------
    datatab : str
        Path to the table with the differences.
    contour : bool, optional
        If True, plots the contours of the distribution.
    colours : str, optional
        Path to the file with the colours.
    savefig : bool, optional
        If True, saves the figure.
    """

    call_logger()
    logger = logging.getLogger(__name__)

    data = pd.read_csv(datatab)
    mask = (data['radiff'] > -10) & (data['radiff'] < 10)
    mask &= (data['dediff'] > -10) & (data['dediff'] < 10)

    radiff = data['radiff'][mask]
    dediff = data['dediff'][mask]
    abspm = data['abspm'][mask]

    percra = np.percentile(radiff, [0.15, 2.5, 16, 50, 84, 97.5, 99.85])
    logger.info('Percentiles for RA: %s' % percra)
    percde = np.percentile(dediff, [0.15, 2.5, 16, 50, 84, 97.5, 99.85])
    logger.info('Percentiles for DEC: %s' % percde)

    left, width = 0.1, 0.6
    bottom, height = 0.1, 0.65
    spacing = 0.005

    rect_scatter = [left, bottom, width, height]
    rect_histx = [left, bottom + height + spacing, width, 0.2]
    rect_histy = [left + width + spacing, bottom, 0.2, height]

    plt.figure(figsize=(9, 8))

    ax_scatter = plt.axes(rect_scatter)
    ax_scatter.tick_params(direction='in', top=True, right=True)
    ax_histx = plt.axes(rect_histx)
    ax_histx.tick_params(direction='in', labelbottom=False)
    ax_histy = plt.axes(rect_histy)
    ax_histy.tick_params(direction='in', labelleft=False)

    lbl = r'$N = %i$' % len(radiff)
    logger.info("Starting plot...")
    sc = ax_scatter.scatter(radiff, dediff, c=abspm,
                            s=10, cmap='plasma', label=lbl)
    logger.info("Finished scatter plot...")
    ax_scatter.grid()
    ax_scatter.legend(loc='upper right', handlelength=0, scatterpoints=1,
                      fontsize=12)
    if contour:
        logger.info("Calculatinig contours...")
        contour_pdf(radiff, dediff, ax=ax_scatter, nbins=100, percent=[0.3, 4.55, 31.7],
                    colors=colours)
        logger.info("Done!")

    cb = plt.colorbar(sc, ax=ax_histy, pad=.02)
    cb.set_label(r'$|\mu|\ \mathrm{[mas\,yr^{-1}]}$', fontsize=20)
    cb.ax.tick_params(labelsize=14)

    # now determine nice limits by hand:
    binwidth = 0.05
    lim = np.ceil(np.abs([radiff, dediff]).max() / binwidth) * binwidth
    ax_scatter.set_xlim((-lim, lim))
    ax_scatter.set_ylim((-lim, lim))
    plt.setp(ax_scatter.get_xticklabels(), fontsize=14)
    plt.setp(ax_scatter.get_yticklabels(), fontsize=14)

    logger.info("Plotting histograms for the percentiles...")
    ax_histx.axvline(percra[0], color='k', linestyle='dashed', lw=1, zorder=1)
    ax_histx.axvline(percra[1], color='k', linestyle='dashed', lw=1, zorder=1)
    ax_histx.axvline(percra[2], color='k', linestyle='dashed', lw=1, zorder=1)
    ax_histx.axvline(percra[3], color='k', linestyle='dashed', lw=1, zorder=1)
    ax_histx.axvline(percra[4], color='k', linestyle='dashed', lw=1, zorder=1)
    ax_histx.axvline(percra[5], color='k', linestyle='dashed', lw=1, zorder=1)
    ax_histx.axvline(percra[6], color='k', linestyle='dashed', lw=1, zorder=1)
    ax_histy.axhline(percde[0], color='k', linestyle='dashed', lw=1, zorder=1)
    ax_histy.axhline(percde[1], color='k', linestyle='dashed', lw=1, zorder=1)
    ax_histy.axhline(percde[2], color='k', linestyle='dashed', lw=1, zorder=1)
    ax_histy.axhline(percde[3], color='k', linestyle='dashed', lw=1, zorder=1)
    ax_histy.axhline(percde[4], color='k', linestyle='dashed', lw=1, zorder=1)
    ax_histy.axhline(percde[5], color='k', linestyle='dashed', lw=1, zorder=1)
    ax_histy.axhline(percde[6], color='k', linestyle='dashed', lw=1, zorder=1)

    # build hists
    logger.info("Building histograms...")
    if radiff.size < 1000000:
        bins = np.arange(-lim, lim + binwidth, binwidth)
    else:
        bins = 1000
    xlbl = "".join([r'$\overline{\Delta\alpha} = %.3f$' % percra[3].value, '\n',
                    r'$\sigma = %.3f$' % np.std(radiff)])
    logger.info("Building RA histogram...")
    xx, xy, _ = ax_histx.hist(radiff, bins=bins, label=xlbl,
                              alpha=0.8, zorder=10)
    ax_histx.legend(loc='upper right', handlelength=0, fontsize=12)
    ylbl = "".join([r'$\overline{\Delta\delta} = %.3f$' % percde[3].value, '\n',
                    r'$\sigma = %.3f$' % np.std(dediff)])
    logger.info("Building DEC histogram...")
    yx, yy, _ = ax_histy.hist(dediff, bins=bins, orientation='horizontal',
                              label=ylbl, alpha=0.8, zorder=10)
    ax_histy.legend(loc='upper right', handlelength=0, fontsize=12)

    ax_histx.set_xlim(ax_scatter.get_xlim())
    ax_histy.set_ylim(ax_scatter.get_ylim())

    # labels
    ax_scatter.set_xlabel(r'$\mathrm{\Delta\alpha\ [arcsec]}$', fontsize=20)
    ax_scatter.set_ylabel(r'$\mathrm{\Delta\delta\ [arcsec]}$', fontsize=20)

    if savefig:
        figpath = datatab.split('.')[0] + '.png'
        logger.info("Saving figure at %s" % figpath)
        plt.savefig(figpath, format='png', dpi=360)
    showfig = True
    if showfig:
        plt.show()
    else:
        plt.close()

    return


def call_logger():
    """Configure the logger."""
    # reset logging config
    logging.shutdown()
    logging.root.handlers.clear()

    # configure the module with colorlog
    logger = colorlog.getLogger()
    logger.setLevel(logging.INFO)

    # create a formatter with green color for INFO level
    formatter = colorlog.ColoredFormatter(
        '%(log_color)s%(levelname)s:%(name)s:%(message)s',
        log_colors={
            'DEBUG':    'cyan',
            'INFO':     'blue',
            'WARNING':  'yellow',
            'ERROR':    'red',
            'CRITICAL': 'red,bg_white',
        })

    # create handler and set the formatter
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)

    # add the handler to the logger
    logger.addHandler(ch)


def get_footprint(foot_path, logger):
    """
    Get the footprint of the survey

    Parameters
    ----------
    footprint : str
        Path to the footprint file

    Returns
    -------
    footprint : astropy Table
        Table containing the footprint of the survey
    """
    path_to_foot = os.path.abspath(foot_path)
    try:
        footprint = ascii.read(path_to_foot)
    except FileNotFoundError:
        logger.error('Footprint file {} not found'.format(path_to_foot))
        sys.exit(1)

    return footprint


def get_fields_names(footprint):
    """
    Get the names of the fields in the footprint and correct them is necessary
    """

    try:
        field_names = np.array([n.replace('_', '-')
                                for n in footprint['NAME']])
    except ValueError:
        field_names = footprint['NAME']

    return field_names


if __name__ == '__main__':
    call_logger()
    # get the path where the code resides
    code_path = os.path.dirname(os.path.abspath(__file__))
    # get the arguments passed from the command line
    args = parser()
    gasp = SplusGaiaAst(args)
    gasp.datadir = args.datadir if args.datadir is not None else args.workdir

    # read text file with the list of tiles to consider
    textfile_path = os.path.join(gasp.workdir, gasp.tiles)
    assert os.path.exists(textfile_path), 'File {} does not exist'.format(
        textfile_path)
    fields = pd.read_csv(textfile_path, sep=' ',
                         header=None, names=['NAME'])

    # get the footprint of the survey
    footprint = get_footprint(args.footprint, logging.getLogger(__name__))
    # get the names of the fields in the footprint
    field_names = get_fields_names(footprint)

    sys.exit()
    # calculate to all tiles at once
    num_procs = 8
    b = list(fields['NAME'])
    if num_procs == 1:
        num_fields = np.unique(b).size
        if num_fields % num_procs > 0:
            print('reprojecting', num_fields, 'fields')
            increase_to = int(num_fields / num_procs) + 1
            i = 0
            while i < (increase_to * num_procs - num_fields):
                b.append('fakename')
                i += 1
            else:
                print(num_fields, 'already fulfill the conditions')
        tiles = np.array(b).reshape(
            (num_procs, int(np.array(b).size / num_procs)))
        print('calculating for a total of', tiles.size, 'fields')
        jobs = []
        print('creating', num_procs, 'jobs...')
        for tile in tiles:
            process = multiprocessing.Process(
                target=gasp.calculate_astdiff, args=(tile, footprint))
            jobs.append(process)

        # start jobs
        print('starting', num_procs, 'jobs!')
        for j in jobs:
            j.start()

        # check if any of the jobs initialized previously still alive
        # save resulting table after all are finished
        proc_alive = True
        while proc_alive:
            if any(proces.is_alive() for proces in jobs):
                proc_alive = True
                time.sleep(1)
            else:
                print('All jobs finished')
                proc_alive = False

        print('Done!')

    if make_plot:
        # to run only after finished all stacking
        # datatab = workdir + 'results/results_stacked.csv'
        datatab = os.path.join(
            workdir, 'mar-astrometry_results_stacked.csv')
        if not os.path.isfile(datatab):
            list_results = glob.glob(
                workdir + 'results/*_mar-gaiaDR3_diff.csv')
            new_tab = pd.read_csv(list_results[0])
            for tab in list_results[1:]:
                print('stacking tab', tab, '...')
                t = pd.read_csv(tab)
                new_tab = pd.concat([new_tab, t], axis=0)
            print('saving results to', datatab)
            new_tab.to_csv(datatab, index=False)

        print('running plot module for table', datatab)
        plot_diffs(datatab, contour=False, colours=[
                   'limegreen', 'yellowgreen', 'c'], savefig=True)
