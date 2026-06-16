from __future__ import annotations

import copy
import io
import torch
from torch import nn

from fl_audit.utils.codec import bytes32_hex


class LogisticRegressionMNIST(nn.Module):
    """Softmax linear classifier for MNIST. Parameter dimension: 784*10+10=7850."""

    def __init__(self):
        super().__init__()
        self.linear = nn.Linear(28 * 28, 10)

    def forward(self, x):
        x = x.view(x.size(0), -1).to(self.linear.weight.dtype)
        return self.linear(x)


class TinyCNNMNIST(nn.Module):
    """Compact CNN for MNIST.

    Architecture:
      Conv(1->4, 3x3) + ReLU + MaxPool
      Conv(4->8, 3x3) + ReLU + MaxPool
      FC(8*7*7 -> 16) + ReLU
      FC(16 -> 10)

    Parameter dimension is 6794, smaller than the linear baseline (7850), so it
    improves representation capacity while keeping cryptographic overhead manageable.
    """

    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 4, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Conv2d(4, 8, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2),
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(8 * 7 * 7, 16),
            nn.ReLU(),
            nn.Linear(16, 10),
        )

    def forward(self, x):
        x = x.to(next(self.parameters()).dtype)
        return self.classifier(self.features(x))


class SmallCNNMNIST(nn.Module):
    """Larger CNN option for stronger accuracy, with higher crypto overhead.

    Parameter dimension is about 26666, so Paillier/Pedersen cost will increase.
    Use it only after TinyCNN runs successfully.
    """

    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 8, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Conv2d(8, 16, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2),
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(16 * 7 * 7, 32),
            nn.ReLU(),
            nn.Linear(32, 10),
        )

    def forward(self, x):
        x = x.to(next(self.parameters()).dtype)
        return self.classifier(self.features(x))


def make_model(name: str = "logistic_regression") -> nn.Module:
    name = str(name).lower()
    if name in {"logistic_regression", "linear", "softmax"}:
        model = LogisticRegressionMNIST()
    elif name in {"tiny_cnn", "cnn_tiny", "cnn"}:
        model = TinyCNNMNIST()
    elif name in {"small_cnn", "cnn_small"}:
        model = SmallCNNMNIST()
    else:
        raise ValueError(
            "Unsupported model.name=%r. Use logistic_regression, tiny_cnn, or small_cnn." % name
        )
    return model.double()


def get_vector(model: nn.Module) -> torch.Tensor:
    parts = [p.detach().cpu().reshape(-1).double() for p in model.parameters()]
    return torch.cat(parts)


def set_vector(model: nn.Module, vector: torch.Tensor) -> None:
    vector = vector.detach().cpu().double()
    idx = 0
    with torch.no_grad():
        for p in model.parameters():
            n = p.numel()
            p.copy_(vector[idx:idx+n].reshape_as(p).to(device=p.device, dtype=p.dtype))
            idx += n


def model_hash(model: nn.Module) -> str:
    return bytes32_hex(get_vector(model).numpy().tolist())


def serialize_model_state(model: nn.Module) -> bytes:
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    return buf.getvalue()


def model_from_serialized_state(template_model: nn.Module, payload: bytes) -> nn.Module:
    model = copy.deepcopy(template_model).cpu()
    try:
        state = torch.load(io.BytesIO(payload), map_location="cpu", weights_only=True)
    except TypeError:
        state = torch.load(io.BytesIO(payload), map_location="cpu")
    model.load_state_dict(state)
    return model.double()


def evaluate(model: nn.Module, loader, device: str = "cpu") -> tuple[float, float]:
    model.to(device)
    model.eval()
    criterion = nn.CrossEntropyLoss(reduction="sum")
    total_loss = 0.0
    correct = 0
    total = 0
    with torch.no_grad():
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            logits = model(x)
            total_loss += float(criterion(logits, y).item())
            pred = logits.argmax(dim=1)
            correct += int((pred == y).sum().item())
            total += int(y.numel())
    return total_loss / max(total, 1), correct / max(total, 1)
