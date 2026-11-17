"""Validate estimator-specific settings shared by the public runner and child processes."""
import hashlib
import math
from pathlib import Path
import re


ESTIMATORS = ("mc", "mnis", "ais", "acs", "hscs")


def require_keys(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown {label} fields: {sorted(unknown)}")


def positive_int(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def finite_number(value, name):
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


def normalize_estimator(estimator, base_directory, budget):
    require_keys(estimator, ("id", "algorithm", "mode", "initialization", "center_selection",
                             "initial_pool", "proposal", "initial_batch", "initial_failures",
                             "estimation_batch", "estimation_draws", "allow_hscs_mixed_search",
                             "hscs_search_origin", "hscs_search_max_simulations"), "estimator")
    result = dict(estimator)
    name = result.get("algorithm")
    identifier = result.get("id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", identifier):
        raise ValueError("Estimator id must be a safe, unique identifier")
    if name not in ESTIMATORS:
        raise ValueError("Invalid estimator algorithm")
    mode = result.get("mode", "reference" if name == "mc" else "legacy")
    if mode not in ("legacy", "reference", "variant"):
        raise ValueError("Estimator mode must be legacy, reference or variant")
    if mode == "reference" and name != "mc":
        raise ValueError("Only direct MC has a registered reference implementation")
    result["mode"] = mode
    strategy_fields = {"initialization", "center_selection", "initial_pool", "proposal", "hscs_search_origin", "hscs_search_max_simulations"}
    if name == "mc":
        if mode == "variant":
            raise ValueError("No configurable direct MC variant is registered")
        if set(result) - {"id", "algorithm", "mode"}:
            raise ValueError("Direct MC uses experiment settings, with no seed or proposal strategies")
        return result
    if ({"allow_hscs_mixed_search", "hscs_search_origin", "hscs_search_max_simulations"} & result.keys()) and name != "hscs":
        raise ValueError("HSCS search options are only valid for HSCS")
    if "allow_hscs_mixed_search" in result and type(result["allow_hscs_mixed_search"]) is not bool:
        raise ValueError("allow_hscs_mixed_search must be boolean")
    for key, default in (("initial_batch", 2), ("initial_failures", 1), ("estimation_batch", 1)):
        result[key] = positive_int(result.get(key, default), key)
    if "estimation_draws" in result:
        count = positive_int(result["estimation_draws"], "estimation_draws")
        if count % result["estimation_batch"]:
            raise ValueError("estimation_draws must be a multiple of estimation_batch")
    if mode == "legacy" and (strategy_fields & result.keys()):
        raise ValueError("Choose variant explicitly when customizing initialization, selection or proposal")
    if mode == "variant":
        result["initialization"] = normalize_search(result.get("initialization"), base_directory, budget)
        if name == "mnis" and result["initial_failures"] != 1:
            raise ValueError("MNIS uses one selected center; set min_failed_candidates to change the candidate count")
        selection_key = "center_selection" if name == "mnis" else "initial_pool"
        incorrect = "initial_pool" if name == "mnis" else "center_selection"
        if incorrect in result:
            raise ValueError(f"{name} uses {selection_key}")
        selection = result.get(selection_key, {})
        require_keys(selection, ("rule",) if name == "mnis" else ("rule", "count"), selection_key)
        rule = selection.get("rule", "discovery_order")
        if rule not in ("discovery_order", "nearest_raw", "nearest_standardized"):
            raise ValueError("Unknown failed-point selection rule")
        count = 1 if name == "mnis" else positive_int(
            selection.get("count", result["initial_failures"]), "initial_pool.count")
        if count > result["initialization"]["min_failed_candidates"]:
            raise ValueError("The selected pool cannot exceed the required candidate count")
        result[selection_key] = {"rule": rule} if name == "mnis" else {"rule": rule, "count": count}
        result["initial_failures"] = count
        result["proposal"] = normalize_proposal(result.get("proposal", {}))
        if name == "hscs":
            origin = result.setdefault("hscs_search_origin", "nominal")
            if origin not in ("legacy", "nominal"):
                raise ValueError("hscs_search_origin must be legacy or nominal")
            if "hscs_search_max_simulations" in result:
                if positive_int(result["hscs_search_max_simulations"], "hscs_search_max_simulations") > budget:
                    raise ValueError("HSCS search simulation cap cannot exceed budget_per_algorithm")
    if name == "ais" and result["estimation_batch"] % result["initial_failures"]:
        raise ValueError("AIS estimation_batch must be a multiple of its initial pool size")
    return result


def normalize_search(search, base_directory, budget):
    require_keys(search, ("method", "radius_sigma", "max_simulations", "min_failed_candidates",
                          "on_insufficient", "path", "sha256"), "initialization")
    result = dict(search)
    method = result.get("method")
    if method not in ("legacy", "uniform_box", "target", "from_file"):
        raise ValueError("initialization.method must be legacy, uniform_box, target or from_file")
    cap = positive_int(result.get("max_simulations"), "initialization.max_simulations")
    count = positive_int(result.get("min_failed_candidates"), "initialization.min_failed_candidates")
    if not count <= cap <= budget:
        raise ValueError("Require min_failed_candidates <= max_simulations <= budget_per_algorithm")
    if result.setdefault("on_insufficient", "stop") != "stop":
        raise ValueError("The supported shortage rule is stop")
    if method == "from_file":
        if not isinstance(result.get("path"), str) or not result["path"]:
            raise ValueError("from_file requires initialization.path")
        path = (base_directory / result["path"]).resolve()
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        if result.get("sha256", checksum) != checksum:
            raise ValueError("Candidate file SHA-256 differs from its frozen configuration")
        result.update(path=str(path), sha256=checksum)
    elif "path" in result or "sha256" in result:
        raise ValueError("Candidate file fields require from_file")
    if method == "uniform_box":
        if finite_number(result.setdefault("radius_sigma", 3.), "radius_sigma") <= 0:
            raise ValueError("radius_sigma must be positive")
    elif "radius_sigma" in result:
        raise ValueError("radius_sigma requires uniform_box")
    return result


def normalize_proposal(proposal):
    require_keys(proposal, ("covariance", "std_scale", "target_fraction", "legacy_variance"), "proposal")
    result = dict(proposal)
    covariance = result.setdefault("covariance", "target_diagonal")
    if covariance not in ("legacy", "target_diagonal"):
        raise ValueError("proposal.covariance must be legacy or target_diagonal")
    if finite_number(result.setdefault("std_scale", 1.), "proposal.std_scale") <= 0:
        raise ValueError("proposal.std_scale must be positive")
    if not 0 <= finite_number(result.setdefault("target_fraction", 0.), "proposal.target_fraction") < 1:
        raise ValueError("proposal.target_fraction must be in [0, 1)")
    if "legacy_variance" in result:
        if covariance != "legacy" or finite_number(result["legacy_variance"], "legacy_variance") <= 0:
            raise ValueError("legacy_variance requires a positive variance and legacy covariance")
    return result


def parse_research_context(research, physical_budget):
    require_keys(research, ("budget_unit", "max_algorithm_draws", "estimator"), "research context")
    unit = research.get("budget_unit")
    if unit not in ("xyce_samples", "algorithm_draws"):
        raise ValueError("budget_unit must be xyce_samples or algorithm_draws")
    max_draws = positive_int(research.get("max_algorithm_draws"), "max_algorithm_draws")
    if (unit == "algorithm_draws" and max_draws != physical_budget
            or unit == "xyce_samples" and max_draws < physical_budget):
        raise ValueError("Draw limit does not agree with the chosen budget unit")
    return dict(budget_unit=unit, max_algorithm_draws=max_draws,
                estimator=normalize_estimator(research.get("estimator"), Path.cwd(), physical_budget))
