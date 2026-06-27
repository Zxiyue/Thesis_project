# fpa5 清理与上传服务器说明

## 1. 本地目录清理目标

根目录建议保留代码与依赖声明，不上传运行缓存、旧结果、数据集和 node_modules。

## 2. configs 清理

正式主实验配置保留：

- configs/mnist_iid_50r_main.yaml
- configs/mnist_noniid_50r_main.yaml
- configs/fashion_mnist_iid_50r_main.yaml
- configs/fashion_mnist_noniid_50r_main.yaml

可选保留回退模板：

- configs/mnist_iid_distributed.yaml
- configs/mnist_noniid_distributed.yaml

其余旧配置先移动到 configs/_archive_old_configs，不建议直接删除。

## 3. Windows 清理

在 fpa5 根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\cleanup_fpa5_safe.ps1
```

## 4. Linux / 服务器清理

在 fpa5 根目录运行：

```bash
bash scripts/cleanup_fpa5_safe.sh
```

## 5. 服务器上传包生成

推荐排除：node_modules、artifacts、cache、outputs、data、虚拟环境、__pycache__、旧 zip。

```bash
zip -r /mnt/fpa5_upload.zip . -x@server_upload_exclude.txt
```

或者使用：

```bash
bash scripts/make_server_zip.sh /mnt/fpa5_upload.zip
```

服务器解压后重新安装依赖：

```bash
pip install -r requirements.txt
npm install
npx hardhat compile
```
