#!/bin/bash
# 假引擎：读一行，回一行。验证 .app 内嵌二进制可被定位并做 stdio 往返。
IFS= read -r line
echo "PONG (got: ${line}, pid: $$, arch: $(uname -m))"
