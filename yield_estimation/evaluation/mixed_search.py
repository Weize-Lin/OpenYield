"""HSCS auxiliary write calls are costs, not read failure observations."""
import csv
import numpy as np
from .run import RecordedReadTestbench, PARAMETER_FORMAT


class RecordedMixedSearchTestbench(RecordedReadTestbench):
    def __init__(self, testbench, output, config):
        super().__init__(testbench, output, config)
        self.write_submitted = self.write_returned = 0
        (output / "auxiliary_write_samples.csv").write_text(
            "sample,batch,xyce_run,write_delay_s,search_condition\n")

    def run_mc_simulation(self, *, operation, target_row, target_col, mc_runs, vars):
        if operation == "read":
            return super().run_mc_simulation(operation=operation, target_row=target_row,
                target_col=target_col, mc_runs=mc_runs, vars=vars)
        if operation != "write" or (target_row, target_col) != (2, 1):
            raise ValueError("HSCS auxiliary search only supports write at [2,1]")
        if self.submitted + mc_runs > self.budget:
            raise RuntimeError("HSCS total read+write budget exceeded")
        samples = np.asarray(vars)
        if samples.shape != (mc_runs, 108) or not np.isfinite(samples).all() or (samples[:,1::3] <= 0).any():
            raise ValueError("Auxiliary write parameters must be finite, 108 dimensional, with positive u0")
        first = self.submitted
        batch = self.output / f"batch_{first:04d}"
        batch.mkdir()
        np.save(batch / "samples.npy", samples)
        self.testbench.sim_path = str(batch)
        self.submitted += mc_runs
        self.write_submitted += mc_runs
        print(f"HSCS auxiliary write: total submitted {self.submitted}/{self.budget}", flush=True)
        try:
            result = self.testbench.run_mc_simulation(operation=operation,
                target_row=target_row, target_col=target_col, mc_runs=mc_runs,
                temperature=self.temperature, vars=samples, process_param_format=PARAMETER_FORMAT)
            delays = np.asarray(result[0], dtype=float).ravel()
            if len(delays) != mc_runs or not np.isfinite(delays).all() or (delays < 0).any():
                raise RuntimeError("Auxiliary write has missing/invalid delay evidence")
        except (Exception, KeyboardInterrupt):
            with (self.output / "auxiliary_write_samples.csv").open("a", newline="") as f:
                csv.writer(f).writerows((first+i+1,batch.name,i,"","unclassified") for i in range(mc_runs))
            raise
        with (self.output / "auxiliary_write_samples.csv").open("a", newline="") as f:
            csv.writer(f).writerows((first+i+1,batch.name,i,float(d),
                "over_search_threshold" if d > self.threshold_s else "within_search_threshold")
                for i,d in enumerate(delays))
        self.returned += mc_runs
        self.write_returned += mc_runs
        return result
