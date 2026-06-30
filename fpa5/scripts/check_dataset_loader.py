from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from torch.utils.data import Subset

from fl_audit.data import make_loaders


def unwrap_dataset(ds):
    while isinstance(ds, Subset):
        ds = ds.dataset
    return ds


def main():
    parser = argparse.ArgumentParser(description="Check whether YAML dataset.name really controls the loaded torchvision dataset.")
    parser.add_argument("--configs", nargs="+", default=[
        "configs/mnist_iid_50r_main.yaml",
        "configs/mnist_noniid_50r_main.yaml",
        "configs/fashion_mnist_iid_50r_main.yaml",
        "configs/fashion_mnist_noniid_50r_main.yaml",
    ])
    args = parser.parse_args()

    for cfg_path in args.configs:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        train_loaders, test_loader, parts = make_loaders(cfg)
        first_client = sorted(train_loaders.keys())[0]
        train_ds = unwrap_dataset(train_loaders[first_client].dataset)
        test_ds = unwrap_dataset(test_loader.dataset)
        print("=" * 88)
        print(f"config: {cfg_path}")
        print(f"configured dataset.name: {cfg['dataset'].get('name')}")
        print(f"actual train dataset class: {type(train_ds).__name__}")
        print(f"actual test dataset class:  {type(test_ds).__name__}")
        print(f"client_1 samples: {len(train_loaders[first_client].dataset)}")
        print(f"test samples:     {len(test_loader.dataset)}")
        print(f"partition clients: {sorted(parts.keys())}")


if __name__ == "__main__":
    main()
