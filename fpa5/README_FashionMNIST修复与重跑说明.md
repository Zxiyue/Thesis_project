# Fashion-MNIST 数据加载修复说明

本包修复了 `fl_audit/data.py` 中固定加载 `datasets.MNIST` 的问题。现在 `make_loaders(cfg)` 会根据 YAML 中的 `dataset.name` 选择：

- `MNIST` -> `torchvision.datasets.MNIST`
- `FashionMNIST` / `Fashion-MNIST` / `Fashion_MNIST` -> `torchvision.datasets.FashionMNIST`

## 新服务器上建议步骤

```bash
cd /root
unzip fpa_fashion_fixed.zip -d /root
mv /root/fpa /root/fpa5 2>/dev/null || true
cd /root/fpa5

python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -r requirements.txt
npm install
npx hardhat compile

mkdir -p /root/fpa5/data
cp -r /public/torchvision_datasets/MNIST /root/fpa5/data/
cp -r /public/torchvision_datasets/FashionMNIST /root/fpa5/data/

python3 scripts/check_dataset_loader.py
```

确认输出中 Fashion 配置的实际类名为：

```text
actual train dataset class: FashionMNIST
actual test dataset class:  FashionMNIST
```

再重跑：

```bash
npx hardhat node
npx hardhat run scripts/deploy.js --network localhost
python3 scripts/start_all_entities.py --config configs/fashion_mnist_iid_50r_main.yaml --clients 5
python3 scripts/start_experiment.py --config configs/fashion_mnist_iid_50r_main.yaml
python3 scripts/collect_results.py --config configs/fashion_mnist_iid_50r_main.yaml || true
python3 scripts/stop_all_entities.py --config configs/fashion_mnist_iid_50r_main.yaml || true
zip -r /root/fashion_mnist_iid_50r_main_results_fixed.zip outputs/fashion_mnist_iid_50r_main
```

Non-IID 同理替换为 `configs/fashion_mnist_noniid_50r_main.yaml`。
