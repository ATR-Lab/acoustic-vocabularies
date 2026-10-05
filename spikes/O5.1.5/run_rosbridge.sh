#!/bin/bash
set -e
# Run only in the approved sidecar sharing the isolated simulator network/IPC.
# Source before nounset: the installed ROS setup refers to unset variables.
source /opt/ros/humble/setup.bash
set -u
params="${1:?Provide the mounted rosbridge-parameters.yaml path}"
exec ros2 run rosbridge_server rosbridge_websocket --ros-args --params-file "$params"
