import numpy as np
from scipy.special import logsumexp
from scipy.stats import multivariate_normal
from unittest.mock import patch
from yield_estimation.model_lib.ACS import ACS
from yield_estimation.tool.Distribution.normal_v1 import norm_dist

centers = np.array([[-2., -1.], [-1.5, -1.2], [1.6, 1.], [2.2, .8]])
probes = np.array([[-2.1, -.9], [0., 0.], [1.7, .7]])
variances = np.array([.5, .75])
target = norm_dist(np.zeros(2), np.eye(2))
expected_beta = multivariate_normal(np.zeros(2), np.eye(2)).pdf(centers)
expected_beta /= expected_beta.sum()
kernels = np.column_stack([
    multivariate_normal(point, np.diag(variances)).pdf(probes) for point in centers
])
flat = kernels @ expected_beta
with patch("subprocess.Popen", side_effect=AssertionError("No subprocesses allowed")):
    beta = ACS._calculate_weight(None, target, centers).ravel()
    np.testing.assert_allclose(beta, expected_beta, rtol=1e-12, atol=0)
    print("Production component weights agree with normalized f(x_i).")
    for labels in (np.array([0, 0, 1, 1]), np.array([0, 1, 0, 1])):
        nested = np.zeros(len(probes))
        for label in np.unique(labels):
            selected = labels == label
            mass = expected_beta[selected].sum()
            local = kernels[:, selected] @ (expected_beta[selected] / mass)
            nested += mass * local
        production = ACS._construct_mixture_norm(None, beta, centers, variances, labels)
        q = np.exp(production.log_pdf(probes).ravel())
        np.testing.assert_allclose(nested, flat, rtol=1e-12, atol=0)
        np.testing.assert_allclose(q, nested, rtol=1e-12, atol=0)
        print("labels =", labels.tolist(), "max |production - nested| =", float(np.max(np.abs(q - nested))))
    sentinel = ACS._construct_mixture_norm(None, beta, centers, variances, None)
    np.testing.assert_allclose(np.exp(sentinel.log_pdf(probes).ravel()), flat, rtol=1e-12, atol=0)
print("Checked 4 fixed centers, 3 probe points, 2 partitions; zero simulations.")
print("This validates one density identity, not full ACS paper equivalence or accuracy.")
