# Notices

## This repository

The documentation, scripts, compose files and configuration templates in this
repository are Copyright (c) 2026 Advantech Co., Ltd. and are licensed under the
MIT License — see [LICENSE](LICENSE).

This repository contains no NeuVector source code or binaries.

## NeuVector (third-party software, not distributed here)

The compose files in this repository reference the official upstream container
image `docker.io/neuvector/allinone`, which the container runtime pulls directly
from the public registry. The image is used **unmodified**; this repository does
not build, alter or redistribute it.

- NeuVector source code is licensed under the Apache License, Version 2.0 —
  <https://github.com/neuvector/neuvector>.
- The image is built on SUSE Linux Enterprise Base Container Images (SLE BCI),
  which are provided under the SUSE BCI EULA (image label `com.suse.eula: sle-bci`).
- The image carries its own license notices: `/licenses/` (NeuVector, server and
  UI dependency licenses) and `/usr/share/licenses/` (operating-system packages).

Anyone who copies the image into another registry (for example a private mirror
for offline devices) is redistributing it and must keep it unmodified, with
those notices intact, under the terms above.

## Trademarks

NeuVector and SUSE are trademarks of SUSE LLC or its affiliates. They are used
here only to identify the third-party software this project deploys. This
project is not affiliated with, sponsored by or endorsed by SUSE, and SUSE does
not provide support for it.

Docker is a trademark of Docker, Inc.
