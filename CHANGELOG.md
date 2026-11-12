# Changelog

## Unreleased

Fixes found by hand-testing on a Surface Laptop 3 (Intel, Kubuntu 26.04, linux-surface 7.2.15-1-surface).

- **Fixed:** "Closing the lid wakes a sleeping device" (blacklists `surface_gpe`) was offered on every Surface that has
  the module, and on the Surface Laptop 3 it breaks lid wake. It is now offered only on the Surface Pro 4 and 5. If it
  is already applied on another model it stays visible, with a warning, so it can still be undone.
- **New fix, Opening the lid doesn't wake the device:** loads `surface_gpe` at every boot
  (`/etc/modules-load.d/50-surface-companion-gpe.conf`, plus a udev rule that sets its wakeup flag where the kernel has
  one) and loads it now. Recommended on Surface Laptop 1 to 3, Surface Book 2 and 3, and Surface Pro 6 and 7. It excludes
  the Pro 4/5 fix: if one is applied the other shows as "Conflicts", and the service refuses to apply it. Counts as
  applied when the module is loaded and not blacklisted, whoever arranged it. On the Surface Laptop 3 the kernel gives
  `surface_gpe` no `power/wakeup` file at all, so there the loaded module is what matters and the wakeup flag is only
  written where the file exists.
- **New fix, Screen stays black after waking (Intel PSR):** adds `i915.enable_psr=0` to the kernel command line with the
  same bootloader helpers as `reboot=pci`. Intel CPUs only; recommended on the Surface Laptop 3. Counts as applied if
  the argument is on the running command line or already in the bootloader configuration. Undo refuses to touch an
  argument you added to the bootloader by hand and says where to remove it.
- **Fixed:** with the GRUB drop-in method, adding a second kernel argument replaced the first one in
  `/etc/default/grub.d/50-surface-companion.cfg`. Arguments now accumulate, and each fix removes only its own.
- **New fix, Keyboard doesn't wake the device:** on Surface laptops whose keyboard is on the Surface Aggregator, finds the
  serial device by its driver (`surface_serial_hub`), not by name, writes a udev rule for it and enables wakeup now.
  Recommended from the Surface Laptop 3 on.
- **New fix, Keyboard doesn't wake the device (Surface Book keyboard base):** the same for the Surface Book 2 and 3's
  USB keyboard base (Microsoft HID keyboard, built in). **Untested on hardware**; optional, never recommended.
- **New advisories:** Surface Book 2 Wi-Fi is unreliable with Bluetooth on (use 5 GHz and WPA2); close anything using the
  NVIDIA GPU before detaching a Surface Book 2 or 3.
- **Fixed, NVIDIA driver on Pascal GPUs:** the open kernel modules that `ubuntu-drivers` usually recommends do not
  support Pascal. For a GPU with a PCI id below 0x1e00, or any Surface Book 2, the plan now installs the proprietary 580
  branch (`nvidia-driver-580` and `nvidia-dkms-580`) whatever `ubuntu-drivers` recommends, and says why. Debian is refused
  when its driver is newer than 580. On Arch the 580 branch exists only in the AUR (`nvidia-580xx-dkms`,
  `nvidia-580xx-utils`), so the plan explains that instead of a `pacman` step that would fail.
- **surface-dtx-daemon is now for the Surface Book 2 and 3.** After installing it the plan enables
  `surface-dtx-daemon.service`, and `surface-dtx-userd.service` with `systemctl --user` for each person logged in (the
  next login if nobody is). The Software page shows whether they are running and offers "Enable services" if not.
- Fix status can now be "Conflicts", shown without an Apply button.

## 0.3.0

- **System test** page. A read-only health check of 18 things (Surface kernel, Surface Aggregator, keyboard and
  touchpad, touchscreen and pen service, brightness, performance profiles, battery health, temperatures and throttling,
  missing firmware, Surface driver errors in the kernel log, Wi-Fi, cameras, discrete GPU, sleep, services, Secure Boot
  key, update source, disk space), each Pass / Warning / Fail / Skipped with a plain explanation and a link to where to
  fix it.
- **Hands-on tests** (press, plug, listen, look; nothing is changed): keyboard (every physical key lights up, by
  hardware key code so it works with any layout), mouse / touchpad / touchscreen, speakers (left, right, both tones),
  ports (live plug and unplug events for USB, USB-C, display outputs and the audio jack), camera (one test photo from
  the camera you pick; kept only in a temporary folder), and a Surface Book screen detach / Type Cover keyboard detach
  test that only watches and never detaches anything itself.
- **Stress test** (Advanced view): loads every CPU core for 30 to 120 seconds, shows temperature, clock speed, fan and
  throttling, and stops by itself above 95 C. Refuses to start on a low battery.
- Copy or save a plain-text report for bug reports.
- Fixed, found by running the new test on a real Surface Laptop 3:
  - The "Device wakes up in a bag" fix never worked: its script had the app's marker line above `#!/bin/sh`, so
    systemd failed with "Exec format error". The shebang is now always the first line, and the fix shows as not applied
    until it is re-applied, so Apply repairs an existing broken install.
  - "Restart touch service" said iptsd was not installed when no instance was running (the normal state after a resume
    or device reset). It now finds the Intel touch controller and starts iptsd for it.
  - Touchpads were counted as touchscreens when deciding whether iptsd is needed.

## 0.2.2

- **System option everywhere the app can override something.** "System" means the app does not control it and
  gives back anything it changed.
  - Performance profile: new System choice (selected by default) and an Automatic choice with one profile for
    battery and one for plugged in, each of which can itself be System. The service switches when the power source
    changes and after resume.
  - Sleep: one "Let the system manage sleep" switch. Turning it on clears the saved sleep mode, removes the app's lid
    action file and puts the lid-wake setting back to what it was before the app first changed it.
  - Screen brightness: "Let the system control brightness" switch; while on, the app never changes brightness.
  - Charge limit: a Limit charging switch (Off = system, resets to 100%), and on devices whose battery driver offers no
    limit (Surface Laptop 3 does not), an unplug reminder notification at a level you choose instead.
  - Discrete GPU power: off now means the system decides.
- Keyboard backlight timeout: turn the backlight off after 5 s to 5 min without activity and back on when you return.
  Works the same on every desktop, including Wayland, because the service listens for input at device level. It only
  starts when you set a timeout, keeps nothing but the time of the last event, and never records what you type.
  Untested on real hardware (needs a Surface with a controllable keyboard backlight).

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
