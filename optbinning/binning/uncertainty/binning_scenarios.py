"""
Optimal binning algorithm given scenarions. Extensive form of the stochastic
optimal binning.
"""

# Guillermo Navas-Palencia <g.navas.palencia@gmail.com>
# Copyright (C) 2020

import numbers
import time

import numpy as np

from sklearn.utils import check_array

from ...information import solver_statistics
from ...logging import Logger
from ...binning.preprocessing import split_data_scenarios
from ..binning import OptimalBinning
from ..binning_statistics import bin_info
from ..binning_statistics import BinningTable
from ..binning_statistics import target_info
from ..cp import BinningCP
from ..prebinning import PreBinning
from ..transformations import transform_binary_target


logger = Logger(__name__).logger


def _check_parameters(name, prebinning_method, max_n_prebins, min_prebin_size,
                      min_n_bins, max_n_bins, min_bin_size, max_bin_size,
                      monotonic_trend, min_event_rate_diff, max_pvalue,
                      max_pvalue_policy, class_weight, user_splits,
                      user_splits_fixed, special_codes, split_digits,
                      time_limit, verbose):

    if not isinstance(name, str):
        raise TypeError("name must be a string.")

    if prebinning_method not in ("cart", "quantile", "uniform"):
        raise ValueError('Invalid value for prebinning_method. Allowed string '
                         'values are "cart", "quantile" and "uniform".')

    if not isinstance(max_n_prebins, numbers.Integral) or max_n_prebins <= 1:
        raise ValueError("max_prebins must be an integer greater than 1; "
                         "got {}.".format(max_n_prebins))

    if not 0. < min_prebin_size <= 0.5:
        raise ValueError("min_prebin_size must be in (0, 0.5]; got {}."
                         .format(min_prebin_size))

    if min_n_bins is not None:
        if not isinstance(min_n_bins, numbers.Integral) or min_n_bins <= 0:
            raise ValueError("min_n_bins must be a positive integer; got {}."
                             .format(min_n_bins))

    if max_n_bins is not None:
        if not isinstance(max_n_bins, numbers.Integral) or max_n_bins <= 0:
            raise ValueError("max_n_bins must be a positive integer; got {}."
                             .format(max_n_bins))

    if min_n_bins is not None and max_n_bins is not None:
        if min_n_bins > max_n_bins:
            raise ValueError("min_n_bins must be <= max_n_bins; got {} <= {}."
                             .format(min_n_bins, max_n_bins))

    if min_bin_size is not None:
        if (not isinstance(min_bin_size, numbers.Number) or
                not 0. < min_bin_size <= 0.5):
            raise ValueError("min_bin_size must be in (0, 0.5]; got {}."
                             .format(min_bin_size))

    if max_bin_size is not None:
        if (not isinstance(max_bin_size, numbers.Number) or
                not 0. < max_bin_size <= 1.0):
            raise ValueError("max_bin_size must be in (0, 1.0]; got {}."
                             .format(max_bin_size))

    if min_bin_size is not None and max_bin_size is not None:
        if min_bin_size > max_bin_size:
            raise ValueError("min_bin_size must be <= max_bin_size; "
                             "got {} <= {}.".format(min_bin_size,
                                                    max_bin_size))

    if monotonic_trend is not None:
        if monotonic_trend not in ("ascending", "descending", "convex",
                                   "concave", "peak", "valley"):
            raise ValueError('Invalid value for monotonic trend. Allowed '
                             'string values are "ascending", "descending", '
                             '"concave", "convex", "peak" and "valley."')

    if (not isinstance(min_event_rate_diff, numbers.Number) or
            not 0. <= min_event_rate_diff <= 1.0):
        raise ValueError("min_event_rate_diff must be in [0, 1]; got {}."
                         .format(min_event_rate_diff))

    if max_pvalue is not None:
        if (not isinstance(max_pvalue, numbers.Number) or
                not 0. < max_pvalue <= 1.0):
            raise ValueError("max_pvalue must be in (0, 1.0]; got {}."
                             .format(max_pvalue))

    if max_pvalue_policy not in ("all", "consecutive"):
        raise ValueError('Invalid value for max_pvalue_policy. Allowed string '
                         'values are "all" and "consecutive".')

    if class_weight is not None:
        if not isinstance(class_weight, (dict, str)):
            raise TypeError('class_weight must be dict, "balanced" or None; '
                            'got {}.'.format(class_weight))

        elif isinstance(class_weight, str) and class_weight != "balanced":
            raise ValueError('Invalid value for class_weight. Allowed string '
                             'value is "balanced".')

    if user_splits is not None:
        if not isinstance(user_splits, (np.ndarray, list)):
            raise TypeError("user_splits must be a list or numpy.ndarray.")

    if user_splits_fixed is not None:
        if user_splits is None:
            raise ValueError("user_splits must be provided.")
        else:
            if not isinstance(user_splits_fixed, (np.ndarray, list)):
                raise TypeError("user_splits_fixed must be a list or "
                                "numpy.ndarray.")
            elif not all(isinstance(s, bool) for s in user_splits_fixed):
                raise ValueError("user_splits_fixed must be list of boolean.")
            elif len(user_splits) != len(user_splits_fixed):
                raise ValueError("Inconsistent length of user_splits and "
                                 "user_splits_fixed: {} != {}. Lengths must "
                                 "be equal".format(len(user_splits),
                                                   len(user_splits_fixed)))

    if special_codes is not None:
        if not isinstance(special_codes, (np.ndarray, list)):
            raise TypeError("special_codes must be a list or numpy.ndarray.")

    if split_digits is not None:
        if (not isinstance(split_digits, numbers.Integral) or
                not 0 <= split_digits <= 8):
            raise ValueError("split_digits must be an integer in [0, 8]; "
                             "got {}.".format(split_digits))

    if not isinstance(time_limit, numbers.Number) or time_limit < 0:
        raise ValueError("time_limit must be a positive value in seconds; "
                         "got {}.".format(time_limit))

    if not isinstance(verbose, bool):
        raise TypeError("verbose must be a boolean; got {}.".format(verbose))


def _check_X_Y_weights(X, Y, weights):
    if not isinstance(X, list):
        raise TypeError("X must be a list of numpy.ndarray.")

    if not isinstance(Y, list):
        raise TypeError("Y must be a list of numpy.ndarray.")

    n_scenarios_x = len(X)
    n_scenarios_y = len(Y)

    if n_scenarios_x != n_scenarios_y:
        raise ValueError("X and Y must have the same length.")

    if weights is not None:
        n_weights = len(weights)
        if n_scenarios_x != n_weights:
            raise ValueError("Number of scenarios and number of weights must "
                             "coincide; got {} != {}."
                             .format(n_scenarios_x, n_weights))


class SBOptimalBinning(OptimalBinning):
    """Scenario-based stochastic optimal binning of a numerical variable with
    respect to a binary target.

    Extensive form of the stochastic optimal binning given a finite number of
    scenarios. The goal is to maximize the expected IV obtaining a solution
    feasible for all scenarios.

    Parameters
    ----------
    name : str, optional (default="")
        The variable name.

    prebinning_method : str, optional (default="cart")
        The pre-binning method. Supported methods are "cart" for a CART
        decision tree, "quantile" to generate prebins with approximately same
        frequency and "uniform" to generate prebins with equal width. Method
        "cart" uses `sklearn.tree.DecistionTreeClassifier
        <https://scikit-learn.org/stable/modules/generated/sklearn.tree.
        DecisionTreeClassifier.html>`_.

    max_n_prebins : int (default=20)
        The maximum number of bins after pre-binning (prebins).

    min_prebin_size : float (default=0.05)
        The fraction of mininum number of records for each prebin.

    min_n_bins : int or None, optional (default=None)
        The minimum number of bins. If None, then ``min_n_bins`` is
        a value in ``[0, max_n_prebins]``.

    max_n_bins : int or None, optional (default=None)
        The maximum number of bins. If None, then ``max_n_bins`` is
        a value in ``[0, max_n_prebins]``.

    min_bin_size : float or None, optional (default=None)
        The fraction of minimum number of records for each bin. If None,
        ``min_bin_size = min_prebin_size``.

    max_bin_size : float or None, optional (default=None)
        The fraction of maximum number of records for each bin. If None,
        ``max_bin_size = 1.0``.

    monotonic_trend : str or None, optional (default=None)
        The **event rate** monotonic trend. Supported trends are "ascending",
        "descending", "concave", "convex", "peak" and "valley". If None, then
        the monotonic constraint is disabled.

    min_event_rate_diff : float, optional (default=0)
        The minimum event rate difference between consecutives bins.

    max_pvalue : float or None, optional (default=None)
        The maximum p-value among bins. The Z-test is used to detect bins
        not satisfying the p-value constraint.

    max_pvalue_policy : str, optional (default="consecutive")
        The method to determine bins not satisfying the p-value constraint.
        Supported methods are "consecutive" to compare consecutive bins and
        "all" to compare all bins.

    class_weight : dict, "balanced" or None, optional (default=None)
        Weights associated with classes in the form ``{class_label: weight}``.
        If None, all classes are supposed to have weight one. Check
        `sklearn.tree.DecistionTreeClassifier
        <https://scikit-learn.org/stable/modules/generated/sklearn.tree.
        DecisionTreeClassifier.html>`_.

    user_splits : array-like or None, optional (default=None)
        The list of pre-binning split points when ``dtype`` is "numerical" or
        the list of prebins when ``dtype`` is "categorical".

    user_splits_fixed : array-like or None (default=None)
        The list of pre-binning split points that must be fixed.

    special_codes : array-like or None, optional (default=None)
        List of special codes. Use special codes to specify the data values
        that must be treated separately.

    split_digits : int or None, optional (default=None)
        The significant digits of the split points. If ``split_digits`` is set
        to 0, the split points are integers. If None, then all significant
        digits in the split points are considered.

    time_limit : int (default=100)
        The maximum time in seconds to run the optimization solver.

    verbose : bool (default=False)
        Enable verbose output.
    """
    def __init__(self, name="", prebinning_method="cart", max_n_prebins=20,
                 min_prebin_size=0.05, min_n_bins=None, max_n_bins=None,
                 min_bin_size=None, max_bin_size=None, monotonic_trend=None,
                 min_event_rate_diff=0, max_pvalue=None,
                 max_pvalue_policy="consecutive", class_weight=None,
                 user_splits=None, user_splits_fixed=None, special_codes=None,
                 split_digits=None, time_limit=100, verbose=False):

        self.name = name
        self.dtype = "numerical"
        self.prebinning_method = prebinning_method
        self.solver = "cp"

        self.max_n_prebins = max_n_prebins
        self.min_prebin_size = min_prebin_size

        self.min_n_bins = min_n_bins
        self.max_n_bins = max_n_bins
        self.min_bin_size = min_bin_size
        self.max_bin_size = max_bin_size

        self.monotonic_trend = monotonic_trend
        self.min_event_rate_diff = min_event_rate_diff
        self.max_pvalue = max_pvalue
        self.max_pvalue_policy = max_pvalue_policy

        self.class_weight = class_weight

        self.user_splits = user_splits
        self.user_splits_fixed = user_splits_fixed
        self.special_codes = special_codes
        self.split_digits = split_digits

        self.time_limit = time_limit

        self.verbose = verbose

        # auxiliary
        self._categories = None
        self._cat_others = None
        self._n_scenarios = None
        self._n_event = None
        self._n_nonevent = None
        self._n_nonevent_missing = None
        self._n_event_missing = None
        self._n_nonevent_special = None
        self._n_event_special = None
        self._problem_type = "classification"
        self._user_splits = user_splits
        self._user_splits_fixed = user_splits_fixed

        # info
        self._binning_table = None
        self._binning_tables = None
        self._n_prebins = None
        self._n_refinements = 0
        self._n_samples_scenario = None
        self._n_samples = None
        self._optimizer = None
        self._splits_optimal = None
        self._status = None

        # timing
        self._time_total = None
        self._time_preprocessing = None
        self._time_prebinning = None
        self._time_solver = None
        self._time_optimizer = None
        self._time_postprocessing = None

        self._is_fitted = False

    def fit(self, X, Y, weights=None, check_input=False):
        """Fit the optimal binning given a list of scenarios.

        Parameters
        ----------
        X : array-like, shape = (n_scenarios,)
            Lit of training vectors, where n_scenarios is the number of
            scenarios.

        Y : array-like, shape = (n_scenarios,)
            List of target vectors relative to X.

        weights : array-like, shape = (n_scenarios,)
            Scenarios weights. If None, then scenarios are equally weighted.

        check_input : bool (default=False)
            Whether to check input arrays.

        Returns
        -------
        self : SBOptimalBinning
            Fitted optimal binning.
        """
        return self._fit(X, Y, weights, check_input)

    def fit_transform(self, x, X, Y, weights=None, metric="woe",
                      metric_special=0, metric_missing=0, show_digits=2,
                      check_input=False):
        """Fit the optimal binning given a list of scenarios, then
        transform it.

        Parameters
        ----------
        x : array-like, shape = (n_samples,)
            Training vector, where n_samples is the number of samples.

        X : array-like, shape = (n_scenarios,)
            Lit of training vectors, where n_scenarios is the number of
            scenarios.

        Y : array-like, shape = (n_scenarios,)
            List of target vectors relative to X.

        weights : array-like, shape = (n_scenarios,)
            Scenarios weights. If None, then scenarios are equally weighted.

        metric : str (default="woe")
            The metric used to transform the input vector. Supported metrics
            are "woe" to choose the Weight of Evidence, "event_rate" to
            choose the event rate, "indices" to assign the corresponding
            indices of the bins and "bins" to assign the corresponding
            bin interval.

        metric_special : float or str (default=0)
            The metric value to transform special codes in the input vector.
            Supported metrics are "empirical" to use the empirical WoE or
            event rate, and any numerical value.

        metric_missing : float or str (default=0)
            The metric value to transform missing values in the input vector.
            Supported metrics are "empirical" to use the empirical WoE or
            event rate and any numerical value.

        show_digits : int, optional (default=2)
            The number of significant digits of the bin column. Applies when
            ``metric="bins"``.

        check_input : bool (default=False)
            Whether to check input arrays.

        Returns
        -------
        x_new : numpy array, shape = (n_samples,)
            Transformed array.
        """
        return self.fit(X, Y, weights, check_input).transform(
            x, metric, metric_special, metric_missing, show_digits,
            check_input)

    def transform(self, x, metric="woe", metric_special=0,
                  metric_missing=0, show_digits=2, check_input=False):
        """Transform given data to Weight of Evidence (WoE) or event rate using
        bins from the fitted optimal binning.

        Parameters
        ----------
        x : array-like, shape = (n_samples,)
            Training vector, where n_samples is the number of samples.

        metric : str (default="woe")
            The metric used to transform the input vector. Supported metrics
            are "woe" to choose the Weight of Evidence, "event_rate" to
            choose the event rate, "indices" to assign the corresponding
            indices of the bins and "bins" to assign the corresponding
            bin interval.

        metric_special : float or str (default=0)
            The metric value to transform special codes in the input vector.
            Supported metrics are "empirical" to use the empirical WoE or
            event rate and any numerical value.

        metric_missing : float or str (default=0)
            The metric value to transform missing values in the input vector.
            Supported metrics are "empirical" to use the empirical WoE or
            event rate and any numerical value.

        show_digits : int, optional (default=2)
            The number of significant digits of the bin column. Applies when
            ``metric="bins"``.

        check_input : bool (default=False)
            Whether to check input arrays.

        Returns
        -------
        x_new : numpy array, shape = (n_samples,)
            Transformed array.

        Notes
        -----
        Transformation of data including categories not present during training
        return zero WoE or event rate.
        """
        self._check_is_fitted()

        return transform_binary_target(self._splits_optimal, self.dtype, x,
                                       self._n_nonevent, self._n_event,
                                       self.special_codes, self._categories,
                                       self._cat_others, None,
                                       metric, metric_special, metric_missing,
                                       self.user_splits, show_digits,
                                       check_input)

    def _fit(self, X, Y, weights, check_input):
        time_init = time.perf_counter()

        # Check parameters and input arrays
        _check_parameters(**self.get_params())
        _check_X_Y_weights(X, Y, weights)

        self._n_scenarios = len(X)

        if self.verbose:
            logger.info("Optimal binning started.")
            logger.info("Options: check parameters.")

        _check_parameters(**self.get_params())

        # Pre-processing
        if self.verbose:
            logger.info("Pre-processing started.")

        time_preprocessing = time.perf_counter()

        self._n_samples_scenario = [len(x) for x in X]
        self._n_samples = sum(self._n_samples_scenario)

        if self.verbose:
            logger.info("Pre-processing: number of samples: {}"
                        .format(self._n_samples))

        [x_clean, y_clean, x_missing, y_missing, x_special, y_special,
         w] = split_data_scenarios(X, Y, weights, self.special_codes,
                                   check_input)

        self._time_preprocessing = time.perf_counter() - time_preprocessing

        if self.verbose:
            n_clean = len(x_clean)
            n_missing = len(x_missing)
            n_special = len(x_special)

            logger.info("Pre-processing: number of clean samples: {}"
                        .format(n_clean))

            logger.info("Pre-processing: number of missing samples: {}"
                        .format(n_missing))

            logger.info("Pre-processing: number of special samples: {}"
                        .format(n_special))

            logger.info("Pre-processing terminated. Time: {:.4f}s"
                        .format(self._time_preprocessing))

        # Pre-binning
        if self.verbose:
            logger.info("Pre-binning started.")

        time_prebinning = time.perf_counter()

        if self.user_splits is not None:
            user_splits = check_array(
                self.user_splits, ensure_2d=False, dtype=None,
                force_all_finite=True)

            if len(set(user_splits)) != len(user_splits):
                raise ValueError("User splits are not unique.")

            sorted_idx = np.argsort(user_splits)
            user_splits = user_splits[sorted_idx]

            if self.user_splits_fixed is not None:
                self.user_splits_fixed = np.asarray(
                    self.user_splits_fixed)[sorted_idx]

            splits, n_nonevent, n_event = self._prebinning_refinement(
                user_splits, x_clean, y_clean, y_missing, y_special)
        else:
            splits, n_nonevent, n_event = self._fit_prebinning(
                w, x_clean, y_clean, y_missing, y_special, self.class_weight)

        self._n_prebins = len(n_nonevent)

        self._time_prebinning = time.perf_counter() - time_prebinning

        if self.verbose:
            logger.info("Pre-binning: number of prebins: {}"
                        .format(self._n_prebins))
            logger.info("Pre-binning: number of refinements: {}"
                        .format(self._n_refinements))

            logger.info("Pre-binning terminated. Time: {:.4f}s"
                        .format(self._time_prebinning))

        # Optimization
        self._fit_optimizer(splits, n_nonevent, n_event, weights)

        # Post-processing
        if self.verbose:
            logger.info("Post-processing started.")
            logger.info("Post-processing: compute binning information.")

        time_postprocessing = time.perf_counter()

        self._n_nonevent = 0
        self._n_event = 0
        self._binning_tables = []

        min_x = np.inf
        max_x = -np.inf

        for s in range(self._n_scenarios):
            min_xs = x_clean[s].min()
            max_xs = x_clean[s].max()

            if min_xs < min_x:
                min_x = min_xs

            if max_xs > max_x:
                max_x = max_xs

            s_n_nonevent, s_n_event = bin_info(
                self._solution, n_nonevent[:, s], n_event[:, s],
                self._n_nonevent_missing[s], self._n_event_missing[s],
                self._n_nonevent_special[s], self._n_event_special[s], None,
                None, [])

            self._n_nonevent += s_n_nonevent
            self._n_event += s_n_event

            binning_table = BinningTable(
                self.name, self.dtype, self.special_codes,
                self._splits_optimal, s_n_nonevent, s_n_event, min_xs, max_xs,
                None, None, self.user_splits)

            self._binning_tables.append(binning_table)

        self._binning_table = BinningTable(
            self.name, self.dtype, self.special_codes, self._splits_optimal,
            self._n_nonevent, self._n_event, min_x, max_x, None, None,
            self.user_splits)

        self._time_postprocessing = time.perf_counter() - time_postprocessing

        if self.verbose:
            logger.info("Post-processing terminated. Time: {:.4f}s"
                        .format(self._time_postprocessing))

        self._time_total = time.perf_counter() - time_init

        if self.verbose:
            logger.info("Optimal binning terminated. Status: {}. Time: {:.4f}s"
                        .format(self._status, self._time_total))

        # Completed successfully
        self._is_fitted = True

        return self

    def _fit_prebinning(self, weights, x_clean, y_clean, y_missing, y_special,
                        class_weight=None):
        x = []
        y = []
        for s in range(self._n_scenarios):
            x.extend(x_clean[s])
            y.extend(y_clean[s])

        x = np.array(x)
        y = np.array(y)

        min_bin_size = int(np.ceil(self.min_prebin_size * self._n_samples))

        prebinning = PreBinning(method=self.prebinning_method,
                                n_bins=self.max_n_prebins,
                                min_bin_size=min_bin_size,
                                problem_type=self._problem_type,
                                class_weight=class_weight).fit(x, y, weights)

        return self._prebinning_refinement(prebinning.splits, x_clean, y_clean,
                                           y_missing, y_special)

    def _prebinning_refinement(self, splits_prebinning, x, y, y_missing,
                               y_special):
        self._n_nonevent_special = []
        self._n_event_special = []
        self._n_nonevent_missing = []
        self._n_event_missing = []
        for s in range(self._n_scenarios):
            s_n_nonevent, s_n_event = target_info(y_special[s])
            m_n_nonevent, m_n_event = target_info(y_missing[s])
            self._n_nonevent_special.append(s_n_nonevent)
            self._n_event_special.append(s_n_event)
            self._n_nonevent_missing.append(m_n_nonevent)
            self._n_event_missing.append(m_n_event)

        n_splits = len(splits_prebinning)

        if not n_splits:
            return splits_prebinning, np.array([]), np.array([])

        if self.split_digits is not None:
            splits_prebinning = np.round(splits_prebinning, self.split_digits)

        splits_prebinning, n_nonevent, n_event = self._compute_prebins(
            splits_prebinning, x, y)

        return splits_prebinning, n_nonevent, n_event

    def _compute_prebins(self, splits_prebinning, x, y):
        n_splits = len(splits_prebinning)

        if not n_splits:
            return splits_prebinning, np.array([]), np.array([])

        n_bins = n_splits + 1
        n_nonevent = np.empty((n_bins, self._n_scenarios)).astype(np.int64)
        n_event = np.empty((n_bins, self._n_scenarios)).astype(np.int64)
        mask_remove = np.zeros(n_bins).astype(bool)

        for s in range(self._n_scenarios):
            y0 = (y[s] == 0)
            y1 = ~y0

            indices = np.digitize(x[s], splits_prebinning, right=False)

            for i in range(n_bins):
                mask = (indices == i)
                n_nonevent[i, s] = np.count_nonzero(y0 & mask)
                n_event[i, s] = np.count_nonzero(y1 & mask)

            mask_remove |= (n_nonevent[:, s] == 0) | (n_event[:, s] == 0)

        if np.any(mask_remove):
            self._n_refinements += 1

            mask_splits = np.concatenate(
                [mask_remove[:-2], [mask_remove[-2] | mask_remove[-1]]])

            if self.user_splits_fixed is not None:
                user_splits_fixed = np.asarray(self._user_splits_fixed)
                user_splits = np.asarray(self._user_splits)
                fixed_remove = user_splits_fixed & mask_splits

                if any(fixed_remove):
                    raise ValueError("Fixed user_splits {} are removed "
                                     "because produce pure prebins. Provide "
                                     "different splits to be fixed."
                                     .format(user_splits[fixed_remove]))

                # Update boolean array of fixed user splits.
                self._user_splits_fixed = user_splits_fixed[~mask_splits]
                self._user_splits = user_splits[~mask_splits]

            splits = splits_prebinning[~mask_splits]

            if self.verbose:
                logger.info("Pre-binning: number prebins removed: {}"
                            .format(np.count_nonzero(mask_remove)))

            [splits_prebinning, n_nonevent, n_event] = self._compute_prebins(
                splits, x, y)

        return splits_prebinning, n_nonevent, n_event

    def _fit_optimizer(self, splits, n_nonevent, n_event, weights):
        time_init = time.perf_counter()

        if not len(n_nonevent):
            self._status = "OPTIMAL"
            self._splits_optimal = splits
            self._solution = np.zeros(len(splits)).astype(bool)

            if self.verbose:
                logger.warning("Optimizer: no bins after pre-binning.")
                logger.warning("Optimizer: solver not run.")

                logger.info("Optimizer terminated. Time: 0s")
            return

        if self.min_bin_size is not None:
            min_bin_size = [int(np.ceil(
                self.min_bin_size * self._n_samples_scenario[s]))
                for s in range(self._n_scenarios)]
        else:
            min_bin_size = self.min_bin_size

        if self.max_bin_size is not None:
            max_bin_size = [int(np.ceil(
                self.max_bin_size * self._n_samples_scenario[s]))
                for s in range(self._n_scenarios)]
        else:
            max_bin_size = self.max_bin_size

        optimizer = BinningCP(self.monotonic_trend, self.min_n_bins,
                              self.max_n_bins, min_bin_size, max_bin_size,
                              None, None, None, None, self.min_event_rate_diff,
                              self.max_pvalue, self.max_pvalue_policy, None,
                              self.user_splits_fixed, self.time_limit)
        if weights is None:
            weights = np.ones(self._n_scenarios, int)

        if self.verbose:
            logger.info("Optimizer: build model...")

        optimizer.build_model_scenarios(n_nonevent, n_event, weights)

        status, solution = optimizer.solve()

        if self.verbose:
            logger.info("Optimizer: solve...")

        self._solution = solution

        self._optimizer, self._time_optimizer = solver_statistics(
            self.solver, optimizer.solver_)
        self._status = status

        self._splits_optimal = splits[solution[:-1]]

        self._time_solver = time.perf_counter() - time_init

        if self.verbose:
            logger.info("Optimizer terminated. Time: {:.4f}s"
                        .format(self._time_solver))

    def binning_table_scenario(self, scenario_id):
        """Return the instantiated binning table corresponding to
        ``scenario_id``. Please refer to :ref:`Binning table: binary target`.

        Parameters
        ----------
        scenario_id : int
            Scenario identifier.

        Returns
        -------
        binning_table : BinningTable
        """
        self._check_is_fitted()

        if (not isinstance(scenario_id, numbers.Integral) or
                not 0 <= scenario_id < self._n_scenarios):
            raise ValueError("scenario_id must be < {}; got {}."
                             .format(self._n_scenarios, scenario_id))

        return self._binning_tables[scenario_id]

    @property
    def splits(self):
        """List of optimal split points.

        Returns
        -------
        splits : numpy.ndarray
        """
        self._check_is_fitted()

        return self._splits_optimal
