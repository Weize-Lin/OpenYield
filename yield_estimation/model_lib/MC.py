import numpy as np
import time
import os
import torch
import random 
from pathlib import Path

from yield_estimation.tool.util import write_data2csv, seed_set


class MC():
    threshold = 3.745662e-09
    _BOUND_FILE_MAP = {
        18: 'model_18_bound.txt',
        108: 'model_108_bound.txt',
        576: 'model_bound_576.txt',
        2304: 'model_bound_2304.txt'
    }
    def __init__(self, f_norm, mc_testbench, means, feature_num, IS_bound_num, initial_num=10000, sample_num=10, FOM_use_num=10, seed=0, IS_bound_on=False,
                 *, target=(1, 1), use_legacy_bounds=True):
        self.x = None   
        self.means = means
        self.feature_num = feature_num
        self.mc_testbench = mc_testbench
        self.target = tuple(target)
        self.use_legacy_bounds = use_legacy_bounds
        self.f_norm = f_norm 
        self.initial_num, self.sample_num, self.FOM_use_num = initial_num, sample_num, FOM_use_num
        self.IS_bound_on = IS_bound_on
        self.IS_bound_num = IS_bound_num
        self.seed = seed
        seed_set(seed)
        if not use_legacy_bounds:
            if IS_bound_on:
                raise ValueError("Clipping requires legacy bounds")
            return
        if self.feature_num not in self._BOUND_FILE_MAP:
            raise ValueError(f"不支持的特征数量: {self.feature_num}，仅支持 {list(self._BOUND_FILE_MAP.keys())}")
        bound_file_path = Path(__file__).resolve().parents[1] / 'bound_lib' / self._BOUND_FILE_MAP[self.feature_num]
        bounds = np.loadtxt(bound_file_path, dtype=np.float64)
        self.up_bounds = bounds[0, :]  
        self.low_bounds = bounds[1, :] 
        self.bound = np.vstack([self.low_bounds, self.up_bounds])
    def save_y_to_txt(self, y, file_path):
        if len(y.shape) != 2 or y.shape[1] != 1:
            raise ValueError("输入的数组不是 n*1 的形状。")
        with open(file_path, 'a') as f:
            np.savetxt(f, y, fmt='%e')
    def indicator(self, y):
        return ~np.isfinite(y) | (y > self.threshold) | (y < 0)
     # Save Monte Carlo simulation results to a CSV file
    def save_result(self, P_fail, FOM, num, used_time):
        data_info_list = [[P_fail], [FOM], [num], [used_time]]

        write_data2csv(tgt_dir=os.path.join("./results/MC"),  
                       tgt_name=f"MC_read_{self.feature_num}.csv",  
                       head_info=('Pfail', 'FOM', 'num', 'used_time'),  
                       data_info=data_info_list)  

     # Private method to extract new samples from the output dataset
    def _get_new_y(self, sample_num, initial_num, i, test_y):   

        if initial_num+(i+1)*sample_num > test_y.shape[0]:
            new_y = test_y[initial_num+i*sample_num:, :]
        else:
            new_y = test_y[initial_num+i*sample_num:initial_num+(i+1)*sample_num, :]
        return new_y  

    # Compute failure probability and Figure of Merit (FOM) based on output data
    def _evaluate(self, y, P_fail_list, FOM_use_num=10): 
        indic = self.indicator(y)
        P_fail = indic.sum() / indic.shape[0]
        P_fail_list = np.hstack([P_fail_list, P_fail])
        # leng = len(P_fail_list)

        if P_fail_list[-FOM_use_num:].mean() == 0:
            FOM = 1
        elif P_fail_list.shape[0] == 1:
            FOM = 1
        else:
            FOM = P_fail_list[-FOM_use_num:].std() / P_fail_list[-FOM_use_num:].mean()
        return P_fail, FOM

  # Extract initial samples from the test_y dataset
    def _get_initial_sample(self, initial_num, test_y):
        return test_y[0:initial_num,:]

    # Randomly shuffle the input test_x data
    def rearrage_x(self, test_x):
        # np.random.shuffle(test_x)
        test_x = np.random.permutation(test_x)
        return test_x
    def _IS_bound(self, x, IS_bound_num):
        low_bound_full = np.ones(x.shape) * self.low_bounds * IS_bound_num
        high_bound_full = np.ones(x.shape) * self.up_bounds * IS_bound_num
        x[x<low_bound_full] = low_bound_full[x<low_bound_full]
        x[x>high_bound_full] = high_bound_full[x>high_bound_full]
        return x
     # Perform Monte Carlo simulation until convergence conditions are met
    def start_estimate(self, max_num=100000, *, fixed_budget=False):
        """Use the configured distribution for every draw in fixed-budget mode."""
        if not fixed_budget and not self.use_legacy_bounds:
            raise ValueError("MC without legacy bounds requires fixed_budget")
        if fixed_budget and (
            max_num < 1 or self.initial_num < 1 or self.sample_num < 1 or self.IS_bound_on
        ):
            raise ValueError('Fixed-budget MC needs positive sample counts and no legacy clipping')
        means = self.means
        feature_num = self.feature_num
        initial_num, sample_num, FOM_use_num = self.initial_num, self.sample_num, self.FOM_use_num
        IS_bound_num, IS_bound_on = self.IS_bound_num, self.IS_bound_on

        delay_output = Path('results/MC/output_read.txt')
        delay_output.parent.mkdir(parents=True, exist_ok=True)
        now_time = time.time()
        self.y, P_fail_list, FOM_list, data_num_list = np.empty([0,1]), np.empty([0]), np.empty([0]), np.empty([0])
        
        if fixed_budget:
            x = self.f_norm.sample(n=min(initial_num, max_num))
        else:
            if feature_num ==18:
                variances = np.abs(means) * 0.003
            elif feature_num ==108:
                variances = np.abs(means) * 0.01
            elif feature_num == 576:
                variances = np.abs(means) * 0.005
            cov_matrix = np.diag(variances)
            x = np.random.multivariate_normal(mean=means, cov=cov_matrix,
                                              size=initial_num)
        if IS_bound_on:
                x = self._IS_bound(x, IS_bound_num)
        num_mc = x.shape[0]
        y, w_pavg, _, _ = self.mc_testbench.run_mc_simulation(operation='read', target_row=self.target[0], target_col=self.target[1], mc_runs=num_mc, vars=x)
        new_y = y.reshape(num_mc,1)
        self.save_y_to_txt(new_y, delay_output)
        self.y = np.vstack([self.y, new_y])
        P_fail, FOM = self._evaluate(self.y, P_fail_list, FOM_use_num)
        P_fail_list = np.hstack([P_fail_list, P_fail])
        self.save_result(P_fail=P_fail, FOM=FOM, num=self.y.shape[0], used_time=time.time() - now_time)
        print(f" # IS sample: {self.y.shape[0]}, fail_rate: {P_fail}, FOM: {FOM}")
        i=0
        while (
            (self.y.shape[0] < max_num and (fixed_budget or FOM > 0.01))
            or (not fixed_budget and i < 40)
        ):
            count = min(sample_num, max_num - self.y.shape[0]) if fixed_budget else sample_num
            x = self.f_norm.sample(n=count)
            if IS_bound_on:
                x = self._IS_bound(x, IS_bound_num)
            num_mc = x.shape[0]
            print(x.shape)
            y, w_pavg, _, _ = self.mc_testbench.run_mc_simulation(operation='read', target_row=self.target[0], target_col=self.target[1], mc_runs=num_mc, vars=x)
            new_y = y.reshape(num_mc,1)
            self.save_y_to_txt(new_y, delay_output)
            self.y = np.vstack([self.y, new_y])

            P_fail, FOM = self._evaluate(self.y, P_fail_list, FOM_use_num)
            P_fail_list = np.hstack([P_fail_list, P_fail])

            self.save_result(P_fail=P_fail, FOM=FOM, num=self.y.shape[0], used_time=time.time() - now_time)
            print(f"[MC] # IS sample: {self.y.shape[0]}, fail_rate: {P_fail}, FOM: {FOM}")
            i += 1

        return P_fail_list, data_num_list
def seed_torch(seed=42):
    seed = int(seed)
    random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.enabled = True
