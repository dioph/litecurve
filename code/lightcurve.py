import numpy as np
import matplotlib.pyplot as plt
from scipy import signal
from scipy.integrate import simps
from astropy.stats import sigma_clip
from astropy.io import ascii, fits
from astroquery.mast import Observations
from tqdm import tqdm

from .utils import DisableLogger
from .corrector import SFFCorrector
import copy
import os
import warnings
warnings.filterwarnings('ignore')

home_dir = os.getenv('home')


class LightCurve(object):
    """
    Implements a simple class for a generic light curve
    
    Attributes
    ----------
    time : array-like
    flux : array-like
    """
    def __init__(self, time, flux):
        self.time = time
        self.flux = flux

    def __getitem__(self, key):
        lc = copy.copy(self)
        lc.time = self.time[key]
        lc.flux = self.flux[key]
        return lc

    def normalize(self):
        """
        Returns a normalized version of the lightcurve
        obtained dividing `flux` by the median flux
        
        Returns
        -------
        lc : LightCurve object 
        """
        lc = copy.copy(self)
        if np.abs(np.nanmedian(lc.flux)) < 1e-9:
            lc.flux = lc.flux + 1
        else:
            lc.flux = lc.flux / np.nanmedian(lc.flux)
        return lc
        
    def remove_nans(self):
        """
        Removes NaN values from flux array
        """
        return self[~np.isnan(self.flux)]
        
    def remove_outliers(self, sigma=5., return_mask=False):
        """
        Removes outliers from lightcurve
        Parameters
        ----------
        sigma : float, optional
            Number of standard deviations to use for clipping limit.
            Default sigma = 5
        return_mask : bool, optional
            Whether to return outlier_mask with the rejected points masked
            Defaults to False
            
        Returns
        -------
        lc : LightCurve object
        outlier_mask : array-like
            Masked array where the points rejected have been masked
        """
        outlier_mask = sigma_clip(data=self.flux, sigma=sigma).mask
        if return_mask:
            return self[~outlier_mask], outlier_mask
        return self[~outlier_mask]
        
    def bins(self, binsize=13, method='mean'):
        """
        """
        available_methods = ['mean', 'median']
        if method not in available_methods:
            raise ValueError("method must be one of: {}".format(available_methods))
        methodf = np.__dict__['nan' + method]
        n_bins = self.time.size // binsize
        lc = copy.copy(self)
        lc.time = np.array([methodf(a) for a in np.array_split(self.time, n_bins)])
        lc.flux = np.array([methodf(a) for a in np.array_split(self.flux, n_bins)])
        if hasattr(lc, 'centroid_col'):
            lc.centrod_col = np.array([methodf(a) for a in np.array_split(self.centroid_col, n_bins)])
        if hasattr(lc, 'centroid_row'):
            lc.centrod_row = np.array([methodf(a) for a in np.array_split(self.centroid_row, n_bins)])
        return lc
    
    def plot(self, ax=None, *args, **kwargs):
        """
        Plots lightcurve in given ax
        
        Returns
        -------
        ax : axes object
        """
        if ax is None:
            fig, ax = plt.subplots(1)
        ax.plot(self.time, self.flux, *args, **kwargs)
        return ax
        
    def activity_proxy(self, method='dv'):
        """
        Calculates a photometric index for magnetic activity
        
        Parameters
        ----------
        method : string, optional
            The name of the index to be used
            Currently implemented methods:
            --- dv  : (He et al. 2015)
            --- iac : (He et al. 2015)
            Defaults to dv.
        
        Returns
        -------
        act : float
            Photometric activity proxy of lightcurve
        """
        available_methods = ['dv', 'iac']
        dic = {'dv':_dv, 'iac':_iac}
        if method not in available_methods:
            raise ValueError("method must be one of: {}".format(available_methods))
        methodf = dic[method]
        lc = self.normalize()
        lc = lc.remove_nans()
        act = methodf(lc.flux)
        return act
        
    def flatten(self, window_length=101, polyorder=3, **kwargs):
        """
        Removes low-frequency trend using scipy's Savitzky-Golay filter
        
        Returns
        -------
        trend_lc : LightCurve object
            Removed polynomial trend
        flat_lc : LightCurve object
            Detrended lightcurve
        """
        clean_lc = self.remove_nans()
        trend = signal.savgol_filter(x=clean_lc.flux, 
                                    window_length=window_length,
                                    polyorder=polyorder, **kwargs)
        flat_lc = copy.copy(clean_lc)
        trend_lc = copy.copy(clean_lc)
        trend_lc.flux = trend
        flat_lc.flux = clean_lc.flux / trend
        return trend_lc, flat_lc
        
    def fold(self, period, phase=0.0):
        """
        """
        fold_time = ((self.time - phase) / period) % 1
        ids = np.argsort(fold_time)
        return LightCurve(fold_time[ids], self.flux[ids])


class KeplerLightCurve(LightCurve):
    def __init__(self, time, flux, centroid_col, centroid_row):
        super(KeplerLightCurve, self).__init__(time, flux)
        self.centroid_col = centroid_col
        self.centroid_row = centroid_row
        
    def __getitem__(self, key):
        lc = super(KeplerLightCurve, self).__getitem__(key)
        lc.centroid_col = self.centroid_col[key]
        lc.centroid_row = self.centroid_row[key]
        return lc
        
    def correct(self, **kwargs):
        assert np.isnan(self.flux).sum() == 0, "Please remove nans before correcting."
        corrector = SFFCorrector()
        lc_corr = corrector.correct(time=self.time, flux=self.flux, 
            centroid_col=self.centroid_col, centroid_row=self.centroid_row, **kwargs)
        new_lc = copy.copy(self)
        new_lc.time = lc_corr.time
        new_lc.flux = lc_corr.flux
        return new_lc


def create_from_kic(kic, mode='pdc', plsbar=False, quarter=None, campaign=None):
    paths = get_lc_kepler(kic, quarter=quarter, campaign=campaign)
    x, y, ccol, crow = [], [], [], []
    if plsbar:
        bar = tqdm(paths)
    else:
        bar = paths
    for p in bar:
        lc = create_from_file(p, xcol='time', ycol=mode+'sap_flux', mode='kepler')
        lc = lc.normalize()
        x = np.append(x, lc.time)
        y = np.append(y, lc.flux)
        ccol = np.append(ccol, lc.centroid_col)
        crow = np.append(crow, lc.centroid_row)
    return KeplerLightCurve(x, y, ccol, crow)


def create_from_file(filename, xcol='time', ycol='flux', mode='ascii'):
    assert mode in ['ascii', 'fits', 'kepler'], "unknown mode {}".format(mode)
    if mode == 'ascii':
        tbl = ascii.read(filename)
        x = tbl[xcol]
        y = tbl[ycol]
        lc = LightCurve(x, y)
    elif mode == 'fits':
        tbl = fits.open(filename)
        hdu = tbl[1].data
        x = hdu[xcol]
        y = hdu[ycol]
        lc = LightCurve(x, y)
    elif mode == 'kepler':
        tbl = fits.open(filename)
        hdu = tbl[1].data
        x = hdu[xcol]
        y = hdu[ycol]
        ccol = hdu['mom_centr1']
        crow = hdu['mom_centr2']
        lc = KeplerLightCurve(x, y, ccol, crow)
    return lc


def get_lc_kepler(target, quarter=None, campaign=None):
    """
    returns table of LCs from Kepler or K2 for a given target
    """
    
    if 0 < target < 2e8:
        name = 'kplr{:09d}'.format(target)
    elif 2e8 < target < 3e8:
        name = 'ktwo{:09d}'.format(target)
    else:
        #TODO: implement error handling function
        pass
        
    obs = Observations.query_criteria(target_name=name, project=['Kepler', 'K2'])
    products = Observations.get_product_list(obs)
    suffix = 'Lightcurve Long'
    mask = np.array([suffix in fn for fn in products['description']])
    when = campaign if campaign is not None else quarter
    if when is not None:
        mask &= np.array([desc.lower().endswith('q{}'.format(when)) or
                            desc.lower().endswith('c{:02}'.format(when)) or
                            desc.replace('-','').lower().endswith('c{:03d}'.format(when))
                            for desc in products['description']])
    pr = products[mask]
    with DisableLogger():
        dl = Observations.download_products(pr, mrp_only=False, download_dir=home_dir)
    return [path[0] for path in list(dl)]


def acf(y, maxlag=None, plssmooth=True):
    """
    Auto-Correlation Function of signal
    Parameters
    ----------
    y : array-like
        Signal
    maxlag : int, optional
        Maximum lag to compute ACF. Defaults to len(y)
    plssmooth : bool, optional
        Whether to smooth ACF using a gaussian kernel.

    Returns
    -------
    R : array-like
        First ``maxlag'' samples of ACF(y)
    """
    N = len(y)
    if maxlag is None:
        maxlag = N
    f = np.fft.fft(y-y.mean(), n=2*N)
    R = np.fft.ifft(f * np.conjugate(f))[:maxlag].real
    R /= R[0]
    if plssmooth:
        h = make_gauss(0, 9)
        h = h(np.arange(-28, 28, 1.))
        R = smooth(R, kernel=h)
    return R


def _dv(y):
    rms = np.sqrt(np.mean(np.square(y-np.median(y))))
    return np.sqrt(8) * rms


def _iac(y):
    N = len(y)
    ml = N//2 + 1
    R = acf(y, maxlag=ml)
    ic = 2/N * simps(R, np.arange(0, ml, 1))
    return ic


def make_gauss(m, s):
    """
    Basic 1-D Gaussian implementation
    Parameters
    ----------
    m : float, array-like
        Mean (location parameter)
    s : float, array-like
        Standard deviation (scale parameter)
    
    Returns
    -------
    gauss : callable
        A gaussian function on a variable x with given parameters
    """
    def gauss(x):
        return 1/(np.sqrt(2*np.pi)*s) * np.exp(-0.5*((x-m)/s)**2)
    return gauss


def smooth(y, kernel):
    """
    Smooths a signal with a kernel. Wraps astropy.convolution.convolve
    
    Parameters
    ----------
    y : array-like
        Raw signal
    scale : int
        Scale parameter of filter (e.g. width, stddev)
    kernel : str, optional
        Kernel used to convolve signal. Default: ``boxcar''

    Returns
    -------
    s : array-like
        Filtered signal
    """

    N = len(y)
    double_y = np.zeros(2*N)
    double_y[:N] = y[::-1]
    double_y[N:] = y

    ys = np.convolve(double_y, kernel, mode='same')
    ys = ys[N:]

    return ys


def filt(y, lo, hi, fs, order=5):
    """
    Filters a signal with a 5th order butterworth passband digital filter
    
    Parameters
    ----------
    y : array-like
        Signal to be filtered
    lo : float
        Lower critical frequency with -3dB
    hi : float
        Higher critical frequency with -3dB
    fs : float
        Sampling frequency (fs > hi > lo)
    
    Returns
    -------
    yf : array-like
        Filtered signal
    """
    nyq = 0.5 * fs
    lo /= nyq
    hi /= nyq
    b, a = signal.butter(order, [lo, hi], btype='band')
    yf = signal.lfilter(b, a, y)
    return yf          

