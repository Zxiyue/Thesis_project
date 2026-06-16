from __future__ import annotations

import copy
import torch
from torch import nn

from fl_audit.model import get_vector, set_vector


def train_local(global_model, loader, epochs: int, lr: float, device: str = "cpu"):
    model = copy.deepcopy(global_model).to(device)
    model.train()
    opt = torch.optim.SGD(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()
    total_loss = 0.0
    steps = 0
    for _ in range(epochs):
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            opt.zero_grad()
            loss = criterion(model(x), y)
            loss.backward()
            opt.step()
            total_loss += float(loss.item())
            steps += 1
    return model, total_loss / max(steps, 1)


def weighted_update(global_model, local_model, weight: float):
    return (get_vector(local_model) - get_vector(global_model)) * float(weight)
