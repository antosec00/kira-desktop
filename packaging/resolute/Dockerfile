ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21
ARG BASE_IMAGE=ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7
FROM ${UV_IMAGE} AS uv
FROM ${BASE_IMAGE}

COPY --from=uv /uv /usr/local/bin/uv
ENV DEBIAN_FRONTEND=noninteractive UV_PYTHON_DOWNLOADS=never UV_NO_CONFIG=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install --no-install-recommends -y \
    python3 python3-venv ca-certificates dpkg-dev git file \
    libc6 libstdc++6 libgcc-s1 libglib2.0-0t64 libegl1 libgl1 libopengl0 \
    libfontconfig1 libdbus-1-3 libgssapi-krb5-2 libharfbuzz0b libxkbcommon0 \
    libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-render-util0 libxcb-xinerama0 libxcb-xkb1 libxcb-shape0 libxcb-randr0 \
    libxcb-sync1 libxcb-render0 libxcb-shm0 libx11-xcb1 libxcb1 libx11-6 libxext6 \
    libwayland-client0 libwayland-cursor0 libwayland-egl1 xdg-utils \
    fonts-noto-core xvfb xauth dbus-x11 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
