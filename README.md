# YouTube Player for Omarchy

A compact YouTube player for the Omarchy bar.

![YouTube Player search panel](screenshots/search.png)
![YouTube Player PiP window](screenshots/player.png)

## What it does

YouTube Player keeps public YouTube playback close to the desktop without turning the bar into a media dashboard:

- search YouTube from a compact Omarchy popup;
- play a public video in `mpv` while other applications stay usable;
- keep playback running when the popup closes;
- expose title and playback state through the native MPRIS ecosystem;
- open a deliberate 480x270 always-on-top PiP window without stealing focus;
- toggle fullscreen from the popup when a larger view is wanted;
- use the system language for user-facing copy: English or Spanish.

The current release is intentionally URL-first and unauthenticated. It saves only the last public video metadata locally. It does not read browser cookies, request Google OAuth, download videos, proxy YouTube, or ship an ad blocker.

Direct stream resolution is not a guarantee of ad-free playback. YouTube playback, availability, authentication, and advertising behavior remain controlled by YouTube. When direct playback fails, open the canonical URL in a normal browser.

## Requirements

- Omarchy with Quickshell plugins
- `mpv`
- `yt-dlp`
- `ffmpeg`
- `mpv-mpris` is recommended for MPRIS metadata and controls

No sudo or pkexec is required.

## Install

```bash
omarchy plugin add https://github.com/brm-src/omarchy-youtube-player.git --enable --yes
omarchy restart shell
```

The plugin manager installs the repository and enables the bar widget. It does not install system packages. Install the requirements with your distribution package manager if they are missing.

To add the widget to a bar section:

```bash
omarchy bar put io.github.brm-src.omarchy-youtube-player --section right
omarchy restart shell
```

For deterministic English screenshots and QA, regardless of the machine locale:

```bash
hyprctl eval 'hl.env("OMARCHY_YOUTUBE_PLAYER_LANG", "en")'
omarchy restart shell
```

Omit that environment variable to use the system language.

## Use

- Click the bar icon to open the player.
- Search for a video, then select a result.
- The video opens in a small native `mpv` window and continues playing after the popup closes.
- Use the popup controls for pause, seek, volume, stop, and fullscreen.
- Close the `mpv` window normally, or use Stop before quitting the shell.

The direct IPC target is:

```bash
omarchy-shell io.github.brm-src.omarchy-youtube-player open
omarchy-shell io.github.brm-src.omarchy-youtube-player close
omarchy-shell io.github.brm-src.omarchy-youtube-player toggle
```

## Privacy and scope

Search and metadata requests go directly to YouTube through `yt-dlp`. The plugin does not send data to a third-party backend. Search terms and URLs are visible to YouTube and the network providers involved in the request.

This plugin is not affiliated with YouTube, Google, or Omarchy. Respect YouTube's Terms of Service, copyright, and the rights of creators.

## Development checks

```bash
python3 -m unittest discover -s tests -q
python3 -m py_compile player.py
qmllint -I /usr/share/omarchy/shell Widget.qml
omarchy plugin validate .
git diff --check
```

## License

MIT. See [LICENSE](LICENSE).
