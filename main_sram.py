#!/usr/bin/env python3
"""OpenYield main entrance: generate one SRAM array deck and simulate it with Xyce.

Per-device local mismatch is the default variation model. The PDK corner stays
fixed while every retained MOS receives independent ``vth0``, ``u0`` and ``voff``
draws, and the fixed ``MC_SEED`` makes the sample reproducible. Configuration is
read from the tracked YAML files in memory; the settings below override the
array geometry and 6T cell sizes without rewriting those files.

Edit the settings block, then run from the repository root inside the
``openyield`` conda environment (Xyce must be on PATH):

    python main_sram.py

For batch or scripted generation use ``python -m sram_compiler.per_device_mc.run``,
which exposes the same defaults as command-line options.
"""
from __future__ import annotations

import os
import argparse
import math
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Sequence

import numpy as np
from PySpice.Unit import u_Ohm, u_pF

from sram_compiler.config_yaml.config import SRAM_CONFIG
from sram_compiler.equivalent_modeling import resolve_equivalent
from sram_compiler.interconnect import load_interconnect, resolve_interconnect
from sram_compiler.per_device_mc.run import get_custom_vars, load_config, resolve_mc_runs
from sram_compiler.testbenches.sram_6t_core_MC_testbench import (
    TRANSIENT_OPERATIONS, Sram6TCoreMcTestbench, full_device_coverage)
from sram_compiler.version import VERSION
from utils import estimate_bitcell_area  # type: ignore

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# ============================ Settings ============================
# Array architecture: [num_rows, num_cols, choose_columnmux]
ARRAY: List[Any] = [16, 16, False]
CORNER = "TT"                   # PDK corner: TT / FF / SS / FS / SF
# 6T cell, applied when global.yaml selects SRAM_6T_CELL (widths/length in metres):
# [pd_width, pg_width, pu_width, length, pd_model, pg_model, pu_model]
CELL_6T: List[Any] = [2.05e-7, 1.35e-7, 9.0e-8, 50.0e-9, "NMOS_VTG", "NMOS_VTG", "PMOS_VTG"]
OPERATION = "write"             # read | write | read&write | hold_snm | read_snm | write_snm
VARIATION_MODE = "per-device"   # per-device (default) | nominal | shared | custom
MC_RUNS: Optional[int] = None   # None: monte_carlo_runs from global.yaml
MC_SEED: Optional[int] = 20260711  # Xyce sampling seed; None draws a new seed every run
RUN_XYCE = True                 # False: generate the deck without simulating it
# Equivalent array model; None keeps the `equivalent` block of global.yaml
# (0: full transistor array, complete local coverage; 1-4: equivalent cells for
# the unused array, an approximation). See sram_compiler/equivalent_modeling/.
REAL_CELL_MODE: Optional[int] = None
W_RC = True                     # Local storage/peripheral series RC (100 ohm / 1 fF)
# Interconnect YAML mapping applied in memory (see docs/design/DISTRIBUTED_RC_MODEL.md),
# e.g. "sram_compiler/config_yaml/interconnect_example.yaml"; None keeps global.yaml's
# `interconnect` block (distributed wiring with illustrative geometry by default).
INTERCONNECT_CONFIG: Optional[str] = None
TARGET: Optional[Sequence[int]] = None  # (row, col); None: last row, last column
# ==================================================================


def configure(rows: int, cols: int, choose_columnmux: bool, corner: str,
              cell_6t: Optional[Sequence[Any]] = None,
              interconnect_config: Optional[str] = INTERCONNECT_CONFIG, *,
              cell_type=None, vdd=None, temperature_c=None) -> SRAM_CONFIG:
    """Load the tracked YAML files in memory and apply the script settings."""
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
           for value in (rows, cols)):
        raise ValueError('ARRAY rows and columns must be positive integers')
    if not isinstance(choose_columnmux, bool):
        raise ValueError('ARRAY column_mux must be a boolean')
    config = load_config(rows, cols, corner)
    config.global_config.choose_columnmux = choose_columnmux
    if cell_type is not None:
        config.global_config.sram_cell_type = cell_type
    if vdd is not None:
        config.global_config.vdd = vdd
    if temperature_c is not None:
        config.global_config.temperature = temperature_c
    if interconnect_config is not None:
        config.global_config.interconnect = load_interconnect(interconnect_config)
    if cell_6t is not None and config.global_config.sram_cell_type == "SRAM_6T_CELL":
        if len(cell_6t) != 7:
            raise ValueError("CELL_6T needs [pd_width, pg_width, pu_width, length, "
                             "pd_model, pg_model, pu_model]")
        pd_width, pg_width, pu_width, length, pd_model, pg_model, pu_model = cell_6t
        if any(not np.isfinite(float(value)) or float(value) <= 0
               for value in (pd_width, pg_width, pu_width, length)):
            raise ValueError('CELL_6T widths and length must be finite and positive metres')
        cell = config.sram_6t_cell
        cell.nmos_width.value = [float(pd_width), float(pg_width)]
        cell.pmos_width.value = float(pu_width)
        cell.length.value = float(length)
        cell.nmos_model.value = [str(pd_model), str(pg_model)]
        cell.pmos_model.value = str(pu_model)
    return config


def normalize_circuit_options(options, defaults):
    """Validate the common circuit block and freeze any external wire config."""
    if not isinstance(options, dict):
        raise ValueError("circuit must be a JSON object")
    unknown = options.keys() - defaults.keys()
    if unknown:
        raise ValueError(f"Unknown circuit options: {sorted(unknown)}")
    resolved = {**defaults, **options}
    array = resolved["array"]
    if (not isinstance(array, (tuple, list)) or len(array) != 3
            or any(type(n) is not int or n < 1 for n in array[:2])
            or type(array[2]) is not bool):
        raise ValueError("circuit.array must be [positive rows, positive columns, boolean column mux]")
    rows, cols, _ = array
    resolved["array"] = list(array)
    target = resolved["target"]
    if target is None:
        target = (rows - 1, cols - 1)
    if (not isinstance(target, (tuple, list)) or len(target) != 2
            or any(type(n) is not int or not 0 <= n < limit
                   for n, limit in zip(target, (rows, cols)))):
        raise ValueError("circuit.target must be [row, column] within the array, starting from zero")
    resolved["target"] = list(target)
    if resolved["cell_type"] not in (None, "SRAM_6T_CELL", "SRAM_10T_CELL"):
        raise ValueError("circuit.cell_type must be SRAM_6T_CELL or SRAM_10T_CELL")
    cell = resolved["cell_6t"]
    if cell is not None:
        if (not isinstance(cell, (tuple, list)) or len(cell) != 7
                or any(type(n) not in (int, float) or not math.isfinite(n) or n <= 0 for n in cell[:4])
                or any(not isinstance(name, str) or not name.strip() for name in cell[4:])):
            raise ValueError("circuit.cell_6t needs four positive sizes in metres and three model names")
        if resolved["cell_type"] == "SRAM_10T_CELL" and options.get("cell_6t") is not None:
            raise ValueError("circuit.cell_6t cannot override a 10T cell")
        resolved["cell_6t"] = list(cell)
    if resolved["corner"] not in ("TT", "FF", "SS", "FS", "SF"):
        raise ValueError("circuit.corner must be TT, FF, SS, FS or SF")
    if resolved["operation"] not in (*TRANSIENT_OPERATIONS, "hold_snm", "read_snm", "write_snm"):
        raise ValueError("Unsupported circuit.operation")
    if resolved["variation_mode"] not in ("nominal", "per-device", "shared", "custom"):
        raise ValueError("Unsupported circuit.variation_mode")
    if resolved["real_cell_mode"] is not None and (
            type(resolved["real_cell_mode"]) is not int or not 0 <= resolved["real_cell_mode"] <= 4):
        raise ValueError("circuit.real_cell_mode must be null or an integer from 0 to 4")
    if type(resolved["w_rc"]) is not bool:
        raise ValueError("circuit.w_rc must be a boolean")
    for key, minimum in (("vdd", 0), ("temperature_c", -273.15)):
        value = resolved[key]
        if value is not None and (type(value) not in (int, float)
                                  or not math.isfinite(value) or value <= minimum):
            raise ValueError(f"circuit.{key} must be finite and greater than {minimum}")
    wiring = resolved["interconnect_config"]
    if isinstance(wiring, str):
        wiring = load_interconnect(wiring)
    if wiring is not None:
        if not isinstance(wiring, dict):
            raise ValueError("circuit.interconnect_config must be a YAML path, a wire mapping, or null")
        resolved["interconnect_config"] = resolve_interconnect(wiring).to_dict()
    return resolved


def configure_circuit(options):
    """Translate the shared options into the compiler's SRAM_CONFIG."""
    wiring = options["interconnect_config"]
    config = configure(
        *options["array"], options["corner"], options["cell_6t"], None,
        cell_type=options["cell_type"], vdd=options["vdd"],
        temperature_c=options["temperature_c"],
    )
    if wiring is not None:
        config.global_config.interconnect = wiring
    return config


def circuit_defaults():
    return dict(array=ARRAY, corner=CORNER, cell_type=None, cell_6t=CELL_6T,
                operation=OPERATION, variation_mode=VARIATION_MODE,
                real_cell_mode=REAL_CELL_MODE, w_rc=W_RC, target=TARGET,
                interconnect_config=INTERCONNECT_CONFIG, vdd=None, temperature_c=None)


def bitcell_area(config: SRAM_CONFIG) -> float:
    cell_type = config.global_config.sram_cell_type
    cell = config.sram_6t_cell if cell_type == "SRAM_6T_CELL" else config.sram_10t_cell
    extra = {} if cell_type == "SRAM_6T_CELL" else {
        "w_fd": cell.nmos_width.value[2], "cell_type": cell_type}
    return estimate_bitcell_area(
        w_access=cell.nmos_width.value[1],
        w_pd=cell.nmos_width.value[0],
        w_pu=cell.pmos_width.value,
        l_transistor=cell.length.value,
        **extra,
    )


def build_testbench(config: SRAM_CONFIG, sim_path: str, *,
                    variation_mode: str = VARIATION_MODE,
                    mc_seed: Optional[int] = MC_SEED,
                    real_cell_mode: Optional[int] = REAL_CELL_MODE,
                    w_rc: bool = W_RC,
                    vth_std: float = .05) -> Sram6TCoreMcTestbench:
    """Build the Monte Carlo testbench with the per-device default made explicit."""
    return Sram6TCoreMcTestbench(
        config,
        sram_cell_type=config.global_config.sram_cell_type,
        w_rc=w_rc,
        pi_res=100 @ u_Ohm, pi_cap=0.001 @ u_pF,
        vth_std=vth_std,  # relative sigma of vth0/u0/voff for shared and per-device modes
        mc=variation_mode in ("shared", "per-device"),
        custom_mc=variation_mode == "custom",
        variation_mode=variation_mode,
        mc_seed=mc_seed,
        sweep_cell=False,
        sweep_precharge=False,
        sweep_senseamp=False,
        sweep_wordlinedriver=False,
        sweep_columnmux=False,
        sweep_writedriver=False,
        sweep_decoder=False,
        corner=config.global_config.corner,
        choose_columnmux=bool(config.global_config.choose_columnmux),
        real_cell_mode=real_cell_mode,
        q_init_val=0,
        sim_path=sim_path,
    )


def _mean(values: Any) -> float:
    samples = np.asarray(values, dtype=float).ravel()
    if not len(samples) or not np.isfinite(samples).all():
        raise RuntimeError('Simulation returned missing or failed samples; inspect the saved results')
    return float(np.mean(samples))


def write_deck(testbench: Sram6TCoreMcTestbench, operation: str,
               target_row: int, target_col: int, mc_runs: int,
               custom_vars: Any) -> Path:
    """Export the same sampled circuit and measures used by a Xyce run."""
    circuit = testbench.create_testbench(operation, target_row, target_col)
    simulator = circuit.simulator(simulator='xyce-serial',
                                  temperature=testbench.temperature,
                                  nominal_temperature=27)
    testbench.add_analysis(simulator.circuit, operation, mc_runs)
    testbench.add_meas_and_print(simulator, testbench.data_init(), operation)
    if custom_vars is not None:
        testbench.gen_process_params(simulator.circuit, operation,
                                     num_mc=mc_runs, vars=custom_vars)
    deck_path = Path(testbench.sim_path) / 'deck.sp'
    deck_path.write_text(str(simulator), encoding='utf-8')
    model_path = Path(getattr(testbench.sram_config.global_config,
                              f'pdk_path_{testbench.corner}'))
    summary = {
        'compiler_version': VERSION,
        'deck': str(deck_path), 'operation': operation,
        'rows': testbench.num_rows, 'cols': testbench.num_cols,
        'target_row': target_row, 'target_col': target_col,
        'cell_type': testbench.sram_cell_type, 'corner': testbench.corner,
        'temperature': testbench.temperature, 'vdd': float(testbench.vdd),
        'variation_mode': testbench.variation_mode, 'mc_runs': mc_runs,
        'full_device_coverage': full_device_coverage(
            testbench.variation_mode, testbench.real_cell_mode, operation),
        'seed': testbench.mc_seed, 'model_sha256': hashlib.sha256(model_path.read_bytes()).hexdigest(),
        'equivalent': testbench.equivalent.to_dict(),
        'interconnect': testbench.interconnect.to_dict(),
        'driver_sizes': testbench.driver_sizes.to_dict(),
        'timing': testbench.timing_config.to_dict(),
        **testbench.variation_summary,
    }
    (Path(testbench.sim_path) / 'summary.json').write_text(
        json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return deck_path


def main(circuit_options=None, *, run_xyce=RUN_XYCE) -> None:
    options = normalize_circuit_options({} if circuit_options is None else circuit_options, circuit_defaults())
    rows, cols, choose_columnmux = options["array"]
    config = configure_circuit(options)
    operation = options["operation"]
    variation_mode = options["variation_mode"]
    real_cell_mode = options["real_cell_mode"]
    cell_6t = options["cell_6t"]
    cell_type = config.global_config.sram_cell_type
    custom_vars = get_custom_vars(config, cell_type) if variation_mode == "custom" else None
    requested = MC_RUNS
    if requested is None and variation_mode in ("shared", "per-device"):
        requested = config.global_config.monte_carlo_runs
    mc_runs = resolve_mc_runs(requested, variation_mode, custom_vars)
    target_row, target_col = options["target"]
    equivalent = resolve_equivalent(config.global_config.equivalent
                                    if real_cell_mode is None else real_cell_mode)

    suffix = "6t" if cell_type == "SRAM_6T_CELL" else "10t"
    time_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    sim_path = os.path.join(_PROJECT_ROOT, "outputs", "main_sram", f"{time_str}_{suffix}")
    os.makedirs(sim_path, exist_ok=True)

    area = bitcell_area(config)
    print(f"Estimated {suffix.upper()} SRAM Cell Area: {area*1e12:.2f} µm²")
    print(f"[INPUT] array: num_rows={rows}, num_cols={cols}, choose_columnmux={choose_columnmux}, "
          f"corner={options['corner']}, cell={cell_type}, target=({target_row}, {target_col})")
    if cell_type == "SRAM_6T_CELL" and cell_6t is not None:
        print(f"[INPUT] sram6tcell_param: pd_width={cell_6t[0]*1e9:.1f} nm, pg_width={cell_6t[1]*1e9:.1f} nm, "
              f"pu_width={cell_6t[2]*1e9:.1f} nm, length={cell_6t[3]*1e9:.1f} nm, "
              f"pd_model={cell_6t[4]}, pg_model={cell_6t[5]}, pu_model={cell_6t[6]}")
    print(f"[INPUT] variation: mode={variation_mode}, mc_runs={mc_runs}, seed={MC_SEED}, "
          f"w_rc={options['w_rc']}, operation={operation}")
    print(f"[INPUT] equivalent: {equivalent.describe()}")
    print(f"[INPUT] interconnect: {resolve_interconnect(config.global_config.interconnect).to_dict()}")

    print(f"===== {suffix.upper()} SRAM Array Monte Carlo Simulation ({variation_mode}) =====")
    testbench = build_testbench(config, sim_path, variation_mode=variation_mode,
                                real_cell_mode=real_cell_mode, w_rc=options["w_rc"])
    temperature = config.global_config.temperature

    if not run_xyce:
        deck_path = write_deck(testbench, operation, target_row, target_col,
                               mc_runs, custom_vars)
        print(f"[OUTPUT] deck: {deck_path}")
        return

    if operation in TRANSIENT_OPERATIONS:
        delay, pavg, pstc, pdyn = testbench.run_mc_simulation(
            operation=operation, target_row=target_row, target_col=target_col,
            mc_runs=mc_runs, temperature=temperature, vars=custom_vars,
        )
        y = np.array([_mean(delay), _mean(np.asarray(pstc) + np.asarray(pdyn)), area])
        print(f"[OUTPUT] mean of {mc_runs} sample(s): y[0]=Delay({y[0]*1e9:.3f} ns), "
              f"y[1]=Power({y[1]*1e6:.2f} uW), y[2]=Area({y[2]*1e12:.2f} µm²)")
    elif operation in ("hold_snm", "write_snm", "read_snm"):
        snm = testbench.run_mc_simulation(
            operation=operation, target_row=target_row, target_col=target_col,
            mc_runs=mc_runs, temperature=temperature, vars=custom_vars,
        )
        print(f"[OUTPUT] {operation}: {snm}")
    else:
        raise ValueError(f"Unknown OPERATION: {operation}")

    print(f"[DEBUG] Monte Carlo simulation completed; outputs in {sim_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--circuit-config", type=Path,
                        help="JSON circuit options or a complete evaluation JSON with a circuit block")
    parser.add_argument("--deck-only", action="store_true", help="Generate the netlist without running Xyce")
    args = parser.parse_args()
    try:
        options = None
        if args.circuit_config is not None:
            data = json.loads(args.circuit_config.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("The configuration must be a JSON object")
            if "circuit" in data:
                options = data["circuit"]
                if not isinstance(options, dict):
                    raise ValueError("circuit must be a JSON object")
                for key in ("corner", "vdd", "temperature_c"):
                    if key in data:
                        if key in options and options[key] != data[key]:
                            raise ValueError(f"Top-level {key} disagrees with circuit.{key}")
                        options[key] = data[key]
                missing = circuit_defaults().keys() - options.keys()
                if missing:
                    raise ValueError(f"Use an explicit circuit block or the saved experiment.json; missing options: {sorted(missing)}")
            else:
                options = data
        main(options, run_xyce=RUN_XYCE and not args.deck_only)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")
