from __future__ import annotations

import random
from typing import Dict, List, Tuple

import torch
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, transforms


SUPPORTED_DATASETS = {
    "mnist": datasets.MNIST,
    "fashionmnist": datasets.FashionMNIST,
    "fashion-mnist": datasets.FashionMNIST,
    "fashion_mnist": datasets.FashionMNIST,
}


def _dataset_class(name: str):
    key = str(name or "MNIST").strip().lower()
    if key not in SUPPORTED_DATASETS:
        raise ValueError(
            f"Unsupported dataset: {name!r}. Supported datasets: MNIST, FashionMNIST"
        )
    return SUPPORTED_DATASETS[key]


def load_dataset(
    name: str,
    data_dir: str,
    max_train_samples: int | None,
    max_test_samples: int | None,
    seed: int,
    download: bool = False,
):
    """Load an image classification dataset used by the FL experiments.

    This function intentionally switches on ``dataset.name`` in YAML. The previous
    implementation always loaded ``datasets.MNIST`` even when the config used
    ``FashionMNIST``. Keeping this logic centralized makes the dataset actually
    match the experiment configuration.
    """
    dataset_cls = _dataset_class(name)
    tfm = transforms.Compose([transforms.ToTensor()])
    train = dataset_cls(data_dir, train=True, download=download, transform=tfm)
    test = dataset_cls(data_dir, train=False, download=download, transform=tfm)

    rng = random.Random(seed)
    if max_train_samples:
        idx = list(range(len(train)))
        rng.shuffle(idx)
        train = Subset(train, idx[:max_train_samples])
    if max_test_samples:
        idx = list(range(len(test)))
        rng.shuffle(idx)
        test = Subset(test, idx[:max_test_samples])
    return train, test


# Backward-compatible alias for older imports or quick tests.
def load_mnist(data_dir: str, max_train_samples: int | None, max_test_samples: int | None, seed: int):
    return load_dataset("MNIST", data_dir, max_train_samples, max_test_samples, seed, download=True)


def _labels(dataset) -> List[int]:
    ys = []
    for i in range(len(dataset)):
        _, y = dataset[i]
        ys.append(int(y))
    return ys


def partition_iid(dataset, clients: int, seed: int) -> Dict[int, List[int]]:
    idxs = list(range(len(dataset)))
    random.Random(seed).shuffle(idxs)
    parts = {i + 1: [] for i in range(clients)}
    for k, idx in enumerate(idxs):
        parts[(k % clients) + 1].append(idx)
    return parts


def partition_noniid(dataset, clients: int, shards_per_client: int, seed: int) -> Dict[int, List[int]]:
    labels = _labels(dataset)
    idxs = list(range(len(dataset)))
    idxs.sort(key=lambda i: labels[i])
    shards = clients * shards_per_client
    shard_size = len(dataset) // shards
    shard_idxs = [idxs[i * shard_size:(i + 1) * shard_size] for i in range(shards)]
    rng = random.Random(seed)
    rng.shuffle(shard_idxs)
    parts = {i + 1: [] for i in range(clients)}
    for c in range(1, clients + 1):
        for _ in range(shards_per_client):
            if shard_idxs:
                parts[c].extend(shard_idxs.pop())
    return parts


def make_loaders(cfg: dict):
    ds_cfg = cfg["dataset"]
    seed = int(cfg.get("seed", 42))
    dataset_name = ds_cfg.get("name", "MNIST")
    download = bool(ds_cfg.get("download", False))
    train, test = load_dataset(
        dataset_name,
        ds_cfg.get("data_dir", "data"),
        ds_cfg.get("max_train_samples"),
        ds_cfg.get("max_test_samples"),
        seed,
        download=download,
    )

    clients = int(cfg["federated"]["clients"])
    if ds_cfg.get("iid", True):
        parts = partition_iid(train, clients, seed)
    else:
        parts = partition_noniid(train, clients, int(ds_cfg.get("shards_per_client", 2)), seed)
    batch_size = int(cfg["federated"].get("batch_size", 64))
    runtime_cfg = cfg.get("runtime", {}) or {}
    num_workers = int(runtime_cfg.get("num_workers", 0))
    pin_memory = bool(runtime_cfg.get("pin_memory", False))
    train_loaders = {
        i: DataLoader(
            Subset(train, idxs),
            batch_size=batch_size,
            shuffle=True,
            num_workers=num_workers,
            pin_memory=pin_memory,
        )
        for i, idxs in parts.items()
    }
    test_loader = DataLoader(
        test,
        batch_size=int(runtime_cfg.get("test_batch_size", 256)),
        shuffle=False,
        num_workers=num_workers,
        pin_memory=pin_memory,
    )
    return train_loaders, test_loader, parts
