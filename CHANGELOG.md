# Changelog

## 0.2.1

- NVIDIA graphics driver install for devices with a discrete NVIDIA GPU (Surface Book, Laptop Studio), on the Software
  page and the Surface Book page. It uses only distribution packages and the DKMS build, because linux-surface is a
  custom kernel: Ubuntu (driver recommended by `ubuntu-drivers` plus its `nvidia-dkms` package), Debian
  (`nvidia-driver`, needs non-free enabled), Arch (`nvidia-dkms`). Fedora is explained, not automated, since it needs the
  third-party RPM Fusion repository. With Secure Boot on, the install opens in a terminal so you can answer the key
  prompts. It shows the commands and notes first. Untested on real NVIDIA hardware.

## 0.2.0

- First-run setup: scans the device step by step (device, system, kernel, touch/pen/camera, fixes, updates), shows
  what you have, which linux-surface kernel is running, installed and available (and the newest published release),
  which features were found, and which fixes apply; then offers to set up the repository, install the kernel and
  touch/pen support, apply updates and recommended fixes (each step shows what runs and asks for your password).
  Unsupported systems are never set up from the wizard; they keep their full warning on the Software page. A guided
  tour follows. Reopen either from "Setup and tour..." at the bottom left.
- CPU, graphics and memory clock speeds with history (Advanced view): per-core CPU clock with governor and limit,
  Intel/AMD integrated graphics clock, memory in use, and the memory speed the firmware configured at boot. Linux has
  no live RAM clock on these machines, and the NVIDIA GPU is not polled so it can stay asleep.
- Simple and Advanced views (bottom left, remembered). Simple shows the essentials and only the recommended fixes;
  Advanced adds history graphs, all sensors, clock speeds, technical rows, community drivers and every fix.

## 0.1.3

- Fixed: "Keyboard at the disk-unlock screen" failed on Ubuntu with `dracut ... image generation failed for kernel
  6.14.0-15-generic`. dracut was installed beside initramfs-tools, and the app picked dracut even though this system's
  images are built by `update-initramfs`; `dracut --regenerate-all` then tripped over a leftover module folder from a
  removed kernel. The app now chooses the tool that actually owns the images in `/boot`, ignores kernel folders that
  have no kernel image, rebuilds dracut images one kernel at a time (a failure on an old kernel no longer fails the
  fix; the running kernel failing does), and rolls its config back if the rebuild fails instead of leaving the fix
  marked as applied.
- The fix now says it is only needed with an encrypted disk, and shows that note when `/etc/crypttab` has no entries.
- Undo also removes a leftover setting written by an earlier run that used a different tool.

## 0.1.2

- The "repository is not set up" warning in the update check now has a Set up repository button beside it.

## 0.1.1

- Fixed: updates were never found when the linux-surface repository had been wiped. A distribution upgrade can leave
  `/etc/apt/sources.list.d/linux-surface.list` behind but blank; the app only checked the file existed, reported the
  repository as set up, and said everything was up to date while the project's newest release was never visible.
  It now reads the file's real content (apt `.list` and `.sources`, dnf, pacman, zypper), shows "Set up repository"
  with a one-click repair, warns on Overview, and the update check says when it cannot see the repository or when
  refreshing it fails instead of reporting "up to date".

## 0.1.0 (unreleased)

First version.

- Controls: screen brightness, keyboard backlight, performance profile, sleep mode, lid behaviour, lid wake,
  battery charge limit (where the kernel offers one), Surface Book clipboard detach and discrete GPU power.
- Read-outs: temperatures, fan speed (when the kernel reports it), battery health, low-power idle residency,
  touch/pen devices, camera devices and drivers.
- Fixes page: workarounds from the linux-surface wiki, filtered to the device, each reversible.
- Software page: installs the linux-surface kernel, iptsd, libwacom-surface, Secure Boot key, surface-control,
  surface-dtx-daemon, thermald and the camera stack on Debian/Ubuntu, Fedora and Arch families, shows the exact
  commands first, and checks for and applies updates to those packages.
- Diagnostics bundle for bug reports.
- Live temperatures (CPU, chassis, SSD, Wi-Fi, GPU, battery), fan speed or fan level where the kernel reports one, and
  CPU thermal throttling, each with about five minutes of history, on Overview and Performance. All raw sensors on
  request. Tray tooltip shows CPU temperature and fan speed.
- Community camera drivers for Surface Pro 5 and Pro 7+ (pinned commits), behind a red warning dialog.
- Installing on unsupported systems (unrecognised distributions with a known package manager, image-based Fedora,
  openSUSE Tumbleweed) behind the same warning; the service refuses unless the warning was accepted.
- Debian package (`packaging/build-deb.sh`). The window also works on Qt older than 6.5 (light/dark inferred from
  the palette).
- Not included: a fan-curve editor. Surface fans are driven by the embedded controller and Linux has no
  interface to change the curve.
