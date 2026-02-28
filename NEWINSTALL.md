```bash
uv venv -p 3.12
source .venv/bin/activate

# install cyclonedds binary globally (remmeber the path)
# get the paths (pacman -Ql cyclonedds | grep -i cmake)
export CMAKE_PREFIX_PATH=/usr
export CycloneDDS_DIR=/usr/lib/cmake/CycloneDDS
# optional but sometimes checked by build scripts:
export CYCLONEDDS_HOME=/usr

uv add cyclonedds
uv pip install -e ./unitree_sdk2_python
uv pip install -e .

uv pip install -r requirements_bridge.txt
uv pip install "mcp[cli]"


python -c "from unitree_sdk2py.go2.sport.sport_client import SportClient; print('SDK OK')"

python -c "import zmq, fastapi, uvicorn; print('Bridge deps OK')"
```
