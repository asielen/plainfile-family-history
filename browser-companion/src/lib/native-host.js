// native-host.js - the optional seamless transport (TOOLING_INGESTION §5.7).
//
// STATUS: the Python side (`fha capture --host` / `fha capture --install-host`,
// §5.7) now ships, so this path is live - but still OFF by default and strictly
// opt-in. The panel uses the §5.1 staging-folder download path unless the human
// turns on "file straight into my archive," which requests the optional
// `nativeMessaging` permission (§5.4) from that click (a user gesture, as Chrome
// requires). Once granted AND a registered host answers a ping, a bundle is
// written straight into the archive's real `inbox/` - anywhere it lives,
// resolved through fha.yaml - with no Downloads detour and no manual `--ingest`.
//
// It is kept here, small, so the panel's hand-off has one clearly marked seam
// rather than the native path being smeared across the UI. Loaded as a classic
// script in panel.html; attaches to the global `FHA`.

(function () {
  const FHA = (window.FHA = window.FHA || {});

  // The native-messaging manifest name the future `fha capture --install-host`
  // registers. Kept as a single constant so the host and extension agree.
  const HOST_NAME = 'com.plaintext.fha_capture';

  // Is the optional `nativeMessaging` permission already granted? (No prompt -
  // a plain check, safe to call on panel load to reflect the toggle state.)
  function hasPermission() {
    return new Promise((resolve) => {
      if (!chrome.permissions) {
        resolve(false);
        return;
      }
      chrome.permissions.contains({ permissions: ['nativeMessaging'] }, (granted) =>
        resolve(!!granted && !chrome.runtime.lastError)
      );
    });
  }

  // Request the optional `nativeMessaging` permission. Chrome requires this be
  // called from a USER GESTURE (a click), so the panel calls it from the
  // "file straight into my archive" toggle's change handler. Resolves to whether
  // the human granted it. Removing the permission again is the toggle-off path.
  function requestPermission() {
    return new Promise((resolve) => {
      if (!chrome.permissions) {
        resolve(false);
        return;
      }
      chrome.permissions.request({ permissions: ['nativeMessaging'] }, (granted) =>
        resolve(!!granted && !chrome.runtime.lastError)
      );
    });
  }

  function removePermission() {
    return new Promise((resolve) => {
      if (!chrome.permissions) {
        resolve(false);
        return;
      }
      chrome.permissions.remove({ permissions: ['nativeMessaging'] }, (removed) =>
        resolve(!!removed && !chrome.runtime.lastError)
      );
    });
  }

  // True only when the human opted into the optional permission AND a host is
  // actually registered (a ping round-trips). Anything less falls back to the
  // download path - the extension never assumes the seamless host is present.
  function isAvailable() {
    return new Promise((resolve) => {
      if (!chrome.permissions || !chrome.runtime || !chrome.runtime.sendNativeMessage) {
        resolve(false);
        return;
      }
      chrome.permissions.contains({ permissions: ['nativeMessaging'] }, (granted) => {
        if (!granted) {
          resolve(false);
          return;
        }
        try {
          chrome.runtime.sendNativeMessage(HOST_NAME, { action: 'ping' }, (resp) => {
            resolve(!chrome.runtime.lastError && !!resp);
          });
        } catch (e) {
          resolve(false);
        }
      });
    });
  }

  // Hand a complete bundle to the host, which files it straight into inbox/.
  // The framing mirrors the staged bundle (§3): page.html + zero-or-more asset
  // files (base64) + capture.json, so the host can reuse run_capture wholesale
  // exactly as `--ingest` does. The `assets` list carries the schema-2 "both"
  // case (a `webpage` page copy and a `record` evidence file). Rejects on any
  // host error so the panel can fall back.
  function sendBundle(spec) {
    return new Promise((resolve, reject) => {
      const message = {
        action: 'ingest',
        bundleName: spec.bundleName,
        pageHtml: spec.pageHtml,
        captureJson: spec.captureJson,
        assets: (spec.assets || []).map((a) => ({
          filename: a.filename, base64: a.base64,
        })),
      };
      try {
        chrome.runtime.sendNativeMessage(HOST_NAME, message, (resp) => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
            return;
          }
          if (!resp || resp.ok === false) {
            reject(new Error((resp && resp.error) || 'the native host could not file the capture'));
            return;
          }
          resolve(resp); // { ok: true, stub: 'inbox/…' }
        });
      } catch (e) {
        reject(e);
      }
    });
  }

  FHA.nativeHost = {
    HOST_NAME, isAvailable, sendBundle,
    hasPermission, requestPermission, removePermission,
  };
})();
