from __future__ import print_function, division

import sys, json
import numpy as np
import scipy.optimize
from ..data import Trace
from ..util.data_test import DataTestCase
from .fitmodel import FitModel
from .searchfit import SearchFit
import warnings


class Psp(FitModel):
    """PSP-like fitting model defined as the product of rising and decaying exponentials.
    
    Parameters
    ----------
    x : array or scalar
        Time values
    xoffset : scalar
        Horizontal shift (positive shifts to the right)
    yoffset : scalar
        Vertical offset
    rise_time : scalar
        Time from beginning of psp until peak
    decay_tau : scalar
        Decay time constant
    amp : scalar
        The peak value of the psp
    rise_power : scalar
        Exponent for the rising phase; larger values result in a slower activation
    
    Notes
    -----
    This model is mathematically similar to the double exponential used in
    Exp2 (the only difference being the rising power). However, the parameters
    are re-expressed to give more direct control over the rise time and peak value.
    This provides a flatter error surface to fit against, avoiding some of the
    tradeoff between parameters that Exp2 suffers from.
    """

    def __init__(self):
        FitModel.__init__(self, self.psp_func, independent_vars=['x'])

    @staticmethod
    def _psp_inner(x, rise, decay, power):
        return (1.0 - np.exp(-x / rise))**power * np.exp(-x / decay)

    @staticmethod
    def _psp_max_time(rise, decay, rise_power):
        """Return the time from start to peak for a psp with given parameters."""
        return rise * np.log(1 + (decay * rise_power / rise))

    @staticmethod
    def psp_func(x, xoffset, yoffset, rise_time, decay_tau, amp, rise_power):
        """Function approximating a PSP shape. 
        """
        rise_tau = Psp._compute_rise_tau(rise_time, rise_power, decay_tau)
        max_val = Psp._psp_inner(rise_time, rise_tau, decay_tau, rise_power)
        
        xoff = x - xoffset
        output = np.empty(xoff.shape, xoff.dtype)
        output[:] = yoffset
        mask = xoff >= 0
        output[mask] = yoffset + (amp / max_val) * Psp._psp_inner(xoff[mask], rise_tau, decay_tau, rise_power)
        
        if not np.all(np.isfinite(output)):
            raise ValueError("Parameters are invalid: xoffset=%f, yoffset=%f, rise_tau=%f, decay_tau=%f, amp=%f, rise_power=%f, isfinite(x)=%s" % (xoffset, yoffset, rise_tau, decay_tau, amp, rise_power, np.all(np.isfinite(x))))
        return output
            
    @staticmethod
    def _compute_rise_tau(rise_time, rise_power, decay_tau):
        fn = lambda tr: tr * np.log(1 + (decay_tau * rise_power / tr)) - rise_time
        return scipy.optimize.fsolve(fn, (rise_time,))[0]


class StackedPsp(FitModel):
    """A PSP on top of an exponential decay.
    
    Parameters are the same as for Psp, with the addition of *exp_amp* and *exp_tau*,
    which describe the baseline exponential decay.
    """
    def __init__(self):
        FitModel.__init__(self, self.stacked_psp_func, independent_vars=['x'])
    
    @staticmethod
    def stacked_psp_func(x, xoffset, yoffset, rise_time, decay_tau, amp, rise_power, exp_amp, exp_tau):
        exp = exp_amp * np.exp(-(x-xoffset) / exp_tau)
        return exp + Psp.psp_func(x, xoffset, yoffset, rise_time, decay_tau, amp, rise_power)


class PspTrain(FitModel):
    """A Train of PSPs, all having the same rise/decay kinetics.
    """
    def __init__(self, n_psp):
        self.n_psp = n_psp
        def fn(*args, **kwds):
            return self.psp_train_func(n_psp, *args, **kwds)
        
        # fn.argnames and fn.kwargs are used internally by lmfit to override
        # its automatic argument detection
        fn.argnames = ['x', 'xoffset', 'yoffset', 'rise_time', 'decay_tau', 'rise_power']
        fn.kwargs = []
        for i in range(n_psp):
            fn.argnames.extend(['xoffset%d'%i, 'amp%d'%i])
            fn.kwargs.append(('decay_tau_factor%d'%i, None))
        
        FitModel.__init__(self, fn, independent_vars=['x'])

    @staticmethod
    def psp_train_func(n_psp, x, xoffset, yoffset, rise_time, decay_tau, rise_power, **kwds):
        """Paramters are the same as for the single Psp model, with the exception
        that the x offsets and amplitudes of each event must be numbered like
        xoffset0, amp0, xoffset1, amp1, etc.
        """
        for i in range(n_psp):
            xoffi = kwds['xoffset%d'%i]
            amp = kwds['amp%d'%i]
            tauf = kwds.get('decay_tau_factor%d'%i, 1)
            psp = Psp.psp_func(x, xoffset+xoffi, 0, rise_time, decay_tau*tauf, amp, rise_power)
            if i == 0:
                tot = psp
            else:
                tot += psp
        
        return tot + yoffset


class Psp2(FitModel):
    """PSP-like fitting model with double-exponential decay.
    
    Shape is computed as the product of a rising exponential and the sum of two decaying exponentials.

    Parameters are xoffset, yoffset, slope, and amp.
    """
    def __init__(self):
        FitModel.__init__(self, self.double_psp_func, independent_vars=['x'])

    @staticmethod
    def double_psp_func(x, xoffset, yoffset, rise_tau, decay_tau1, decay_tau2, amp1, amp2, rise_power=2.0):
        """Function approximating a PSP shape with double exponential decay. 
        """
        x = x-xoffset
        
        out = np.zeros(x.shape, x.dtype)
        mask = x >= 0
        x = x[mask]
        
        rise_exp = (1.0 - np.exp(-x / rise_tau))**rise_power
        decay_exp1 = amp1 * np.exp(-x / decay_tau1)
        decay_exp2 = amp2 * np.exp(-x / decay_tau2)
        out[mask] = riseExp * (decay_exp1 + decay_exp2)

        return out


def fit_psp(data, search_window, clamp_mode, sign=0, exp_baseline=True, baseline_like_psp=False, params=None, fit_kws=None, ui=None):
    """Fit a Trace instance to a StackedPsp model.
    
    This function is a higher-level interface to StackedPsp.fit:    
    * Makes some assumptions about typical PSP/PSC properties based on the clamp mode
    * Uses SearchFit to find a better fit over a wide search window, and to avoid
      common local-minimum traps.

    Parameters
    ----------
    data : neuroanalysis.data.TSeries instance
        Contains data on trace waveform.
    search_window : tuple
        start, stop range over which to search for PSP onset.
    clamp_mode : string
        either 'ic' for current clamp or 'vc' for voltage clamp
    sign : int
        Specifies the sign of the PSP deflection. Must be 1, -1, or 0.
    exp_baseline : bool
        If True, then the pre-response baseline is fit to an exponential decay. 
        This is useful when the PSP follows close after another PSP or action potential.
    baseline_like_psp : bool
        If True, then the baseline exponential tau and psp decay tau are forced to be equal,
        and their amplitudes are forced to have the same sign.
        This is useful in situations where the baseline has an exponential decay caused by a preceding
        PSP of similar shape, such as when fitting one PSP in a train.
    params : dict
        Override parameters to send to the fitting function (see StackedPsp.fit)
    fit_kws : dict
        Extra keyword arguments to send to the minimizer
    
    Returns
    -------
    fit : lmfit.model.ModelResult
        Best fit
    """           
    import pyqtgraph as pg
    prof = pg.debug.Profiler(disabled=False, delayed=False)
    
    if ui is not None:
        ui.clear()
        ui.console.setStack()
        ui.plt1.plot(data.time_values, data.data)
        ui.plt1.addLine(x=search_window[0], pen=0.3)
        ui.plt1.addLine(x=search_window[1], pen=0.3)
        prof('plot')

    if fit_kws is None:
        fit_kws = {}

    method = 'leastsq'
    fit_kws.setdefault('maxfev', 40)

    # good fit, slow
    # method = 'Nelder-Mead'
    
    # fit_kws.setdefault('options', {
    #     'maxiter': 300, 
        
    #     # 'disp': True,
    # })
    
    # good fit
    # method = 'Powell'
    # fit_kws.setdefault('options', {'maxfev': 200, 'disp': True})

    # bad fit
    # method = 'CG'
    # fit_kws.setdefault('options', {'maxiter': 100, 'disp': True})

    # method = 'L-BFGS-B'
    # fit_kws.setdefault('options', {'maxiter': 100, 'disp': True})
    
    # set initial conditions depending on whether in voltage or current clamp
    # note that sign of these will automatically be set later on based on the 
    # the *sign* input
    if clamp_mode == 'ic':
        amp_init = .2e-3
        amp_max = 100e-3
        rise_time_init = 5e-3
        decay_tau_init = 50e-3
        exp_tau_init = 50e-3
    elif clamp_mode == 'vc':
        amp_init = 20e-12
        amp_max = 500e-12
        rise_time_init = 1e-3
        decay_tau_init = 4e-3
        exp_tau_init = 4e-3
    else:
        raise ValueError('clamp_mode must be "ic" or "vc"')

    # Set up amplitude initial values and boundaries depending on whether *sign* are positive or negative
    if sign == -1:
        amp = (-amp_init, -amp_max, 0)
    elif sign == 1:
        amp = (amp_init, 0, amp_max)
    elif sign == 0:
        amp = (0, -amp_max, amp_max)
    else:
        raise ValueError('sign must be 1, -1, or 0')
        
    # initial condition, lower boundary, upper boundary
    base_params = {
        'yoffset': (0, -float('inf'), float('inf')),
        'rise_time': (rise_time_init, rise_time_init/10., rise_time_init*10.),
        'decay_tau': (decay_tau_init, decay_tau_init/10., decay_tau_init*10.),
        'rise_power': (2, 'fixed'),
        'amp': amp,
    }
    
    # specify fitting function and set up conditions
    psp = StackedPsp()
    if exp_baseline:
        if baseline_like_psp:
            base_params.update({
                'exp_amp': "amp * exp_psp_amp_ratio",
                'exp_psp_amp_ratio': (0.01, 0, float('inf')),
                'exp_tau': 'decay_tau',
            })
        else:
            base_params.update({
                'exp_amp': (0, -float('inf'), float('inf')),
                'exp_tau': (exp_tau_init, exp_tau_init / 10., exp_tau_init * 20.)
            })
    else:
        base_params.update({'exp_amp': (0, 'fixed'), 'exp_tau': (1, 'fixed')})
    
    # if weight is None: #use default weighting
    #     weight = np.ones(len(y))
    # else:  #works if there is a value specified in weight
    #     if len(weight) != len(y):
    #         raise Exception('the weight and array vectors are not the same length')     
    # fit_kws['weights'] = weight

    # Round 1: coarse fit

    # Coarse search xoffset
    n_xoffset_chunks = max(1, int((search_window[1] - search_window[0]) / 1e-3))
    xoffset_chunks = np.linspace(search_window[0], search_window[1], n_xoffset_chunks+1)
    xoffset = [{'xoffset': ((a+b)/2., a, b)} for a,b in zip(xoffset_chunks[:-1], xoffset_chunks[1:])]
    
    prof('prep for coarse fit')

    # Find best coarse fit 
    search = SearchFit(psp, [xoffset], params=base_params, x=data.time_values, data=data.data, fit_kws=fit_kws, method=method)
    for i,result in enumerate(search.iter_fit()):
        pass
        # prof('  coarse fit iteration %d/%d: %s %s' % (i, len(search), result['param_index'], result['params']))
    fit = search.best_result.best_values
    prof("coarse fit done (%d iter)" % len(search))

    if ui is not None:
        br = search.best_result
        ui.plt1.plot(data.time_values, br.best_fit, pen=(0, 255, 0, 100))

    # Round 2: fine fit

    # Fine search xoffset
    fine_search_window = (max(search_window[0], fit['xoffset']-1e-3), min(search_window[1], fit['xoffset']+1e-3))
    n_xoffset_chunks = max(1, int((fine_search_window[1] - fine_search_window[0]) / .2e-3))
    xoffset_chunks = np.linspace(fine_search_window[0], fine_search_window[1], n_xoffset_chunks + 1)
    xoffset = [{'xoffset': ((a+b)/2., a, b)} for a,b in zip(xoffset_chunks[:-1], xoffset_chunks[1:])]

    # Search amp / rise time / decay tau to avoid traps
    rise_time_inits = base_params['rise_time'][0] * 1.2**np.arange(-1,6)
    rise_time = [{'rise_time': (x,) + base_params['rise_time'][1:]} for x in rise_time_inits]

    decay_tau_inits = base_params['decay_tau'][0] * 2.0**np.arange(-1,2)
    decay_tau = [{'decay_tau': (x,) + base_params['decay_tau'][1:]} for x in decay_tau_inits]
    
    search_params = [
        rise_time, 
        decay_tau, 
        xoffset,
    ]

    # if no sign was specified, search from both sides    
    if sign == 0:
        amp = [{'amp': (amp_init, -amp_max, amp_max)}, {'amp': (-amp_init, -amp_max, amp_max)}]
        search_params.append(amp)

    prof("prepare for fine fit")

    # Find best fit 
    search = SearchFit(psp, search_params, params=base_params, x=data.time_values, data=data.data, fit_kws=fit_kws, method=method)
    for i,result in enumerate(search.iter_fit()):
        pass
        # prof('  fine fit iteration %d/%d: %s %s' % (i, len(search), result['param_index'], result['params']))
    fit = search.best_result
    prof('fine fit done (%d iter)' % len(search))

    # nrmse = fit.nrmse()
    if 'baseline_std' in data.meta:
        fit.snr = abs(fit.best_values['amp']) / data.meta['baseline_std']
        fit.err = fit.nrmse() / data.meta['baseline_std']

    return fit


class PspFitTestCase(DataTestCase):
    def __init__(self):
        DataTestCase.__init__(self, PspFitTestCase.fit_psp)

    @staticmethod
    def fit_psp(**kwds):
        result = fit_psp(**kwds)
        # for storing / comparing fit results, we need to return a dict instead of ModelResult
        ret = result.best_values.copy()
        ret['nrmse'] = result.nrmse()
        return ret

    @property
    def name(self):
        meta = self.meta
        return "%0.3f_%s_%s_%s_%s" % (meta['expt_id'], meta['sweep_id'], meta['pre_cell_id'], meta['post_cell_id'], meta['pulse_n'])

    def _old_load_file(self, file_path):
        test_data = json.load(open(file_path))
        self._input_args = {
            'data': Trace(data=np.array(test_data['input']['data']), dt=test_data['input']['dt']),
            'xoffset': (14e-3, -float('inf'), float('inf')),
            'weight': np.array(test_data['input']['weight']),
            'sign': test_data['input']['amp_sign'], 
            'stacked': test_data['input']['stacked'],
        }
        self._expected_result = test_data['out']['best_values']
        self._meta = {}
        self._loaded_file_path = file_path

    def load_file(self, file_path):
        DataTestCase.load_file(self, file_path)
        xoff = self._input_args.pop('xoffset', None)
        if xoff is not None:
            self._input_args['search_window'] = xoff[1:]
