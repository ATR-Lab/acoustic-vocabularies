# Installation requires approval. Build has network access; simulation never does.
FROM nvcr.io/nvidia/isaac-sim@sha256:f3563cb2ba0c18af0b2fb321360dcb73a917b899f879e3213623d66bee484fa54
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git=1:2.43.0-1ubuntu7.3
RUN git init /lab && git -C /lab remote add origin https://github.com/isaac-sim/IsaacLab.git \
    && git -C /lab fetch --depth 1 origin 80094be3245aa5c8376a7464d29cb4412ea518f5 \
    && git -C /lab checkout --detach FETCH_HEAD
RUN git init /unitree && git -C /unitree remote add origin https://github.com/unitreerobotics/unitree_sim_isaaclab.git \
    && git -C /unitree fetch --depth 1 origin e30c25b1dffdf92ada1d6c8c1fe9a47bdde0fecc \
    && git -C /unitree checkout --detach FETCH_HEAD
RUN /isaac-sim/python.sh -m pip install --no-build-isolation -e /lab/source/isaaclab h5py==3.15.1
ENTRYPOINT ["/bin/bash"]
