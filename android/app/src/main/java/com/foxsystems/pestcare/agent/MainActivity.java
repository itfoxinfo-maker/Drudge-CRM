package com.foxsystems.pestcare.agent;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.MediaStore;
import android.text.InputType;
import android.webkit.GeolocationPermissions;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import androidx.core.content.FileProvider;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

import java.io.File;

/**
 * Thin native shell around the live PestCare CRM web app. Because it loads the
 * site from the server, any change deployed to the CRM appears instantly — no
 * rebuild / reinstall. The server URL can be changed on-device (long-press the
 * top-left corner) so even moving to a new domain/HTTPS needs no reinstall.
 */
public class MainActivity extends AppCompatActivity {

    // Default address the app loads on first run (changeable in-app).
    private static final String DEFAULT_URL = "http://95.216.189.8:8000";
    private static final String PREFS = "pestcare";
    private static final String KEY_URL = "server_url";
    private static final int REQ_PERMS = 100;

    /**
     * Injected after every page load. The CRM is a single-page app, so this
     * self-installs listeners (history, resize, DOM changes) and re-fits on its
     * own as the agent navigates — no further injection needed.
     *
     * It measures the page at normal device width; if the content is wider than
     * the screen (e.g. a wide data table or the calendar grid), it switches the
     * viewport to that content width so the WebView zooms the WHOLE page down to
     * fit — eliminating the sideways scroll while keeping everything in
     * proportion. A 0.25 minimum-scale floor stops very wide pages becoming
     * unreadably tiny (those keep pinch-zoom as a fallback). A short lock guards
     * against the resize our own viewport change fires, so it can't oscillate.
     */
    private static final String FIT_JS =
            "(function(){" +
            "if(window.__pcFit)return;window.__pcFit=true;" +
            "var BASE='width=device-width, initial-scale=1, minimum-scale=0.25, maximum-scale=5, user-scalable=yes';" +
            "var t,lock=0;" +
            "function mv(){var m=document.querySelector('meta[name=viewport]');" +
            "if(!m){m=document.createElement('meta');m.name='viewport';(document.head||document.documentElement).appendChild(m);}return m;}" +
            "function fit(){var m=mv();lock=Date.now();m.setAttribute('content',BASE);" +
            "requestAnimationFrame(function(){var w=Math.ceil(document.documentElement.scrollWidth);var s=window.innerWidth;" +
            "m.setAttribute('content', w>s+2 ? ('width='+w+', minimum-scale=0.25, maximum-scale=5, user-scalable=yes') : BASE);" +
            "lock=Date.now();});}" +
            "function sch(){if(Date.now()-lock<800)return;clearTimeout(t);t=setTimeout(fit,250);}" +
            "addEventListener('resize',sch);addEventListener('orientationchange',sch);" +
            "addEventListener('hashchange',sch);addEventListener('popstate',sch);" +
            "var p=history.pushState;history.pushState=function(){var r=p.apply(this,arguments);sch();return r;};" +
            "var rp=history.replaceState;history.replaceState=function(){var r=rp.apply(this,arguments);sch();return r;};" +
            "var v=document.getElementById('view')||document.body;" +
            "if(window.MutationObserver&&v){new MutationObserver(sch).observe(v,{childList:true,subtree:true});}" +
            "fit();})();";

    private WebView web;
    private SwipeRefreshLayout swipe;
    private ValueCallback<Uri[]> filePathCallback;
    private Uri cameraImageUri;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        swipe = findViewById(R.id.swipe);
        web = findViewById(R.id.web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(true);
        s.setGeolocationEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        // Fit the web page to the screen width (no sideways scroll) and let the
        // injected fit script (see FIT_JS) zoom the whole page down when its
        // content is wider than the device. useWideViewPort + overview make the
        // WebView honour the viewport width we set from JS and scale to fit.
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        // Keep pinch-zoom available as a manual backup, but hide the +/- buttons.
        s.setSupportZoom(true);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                // Keep everything in-app (same server); let the WebView handle it.
                return false;
            }
            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) {
                    Toast.makeText(MainActivity.this,
                            getString(R.string.cant_reach), Toast.LENGTH_LONG).show();
                }
            }
            @Override
            public void onPageFinished(WebView view, String url) {
                swipe.setRefreshing(false);
                // Squeeze the page to fit the screen width so there's no
                // horizontal scrolling (re-runs itself on in-app navigation).
                view.evaluateJavascript(FIT_JS, null);
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            // Photo / file upload from report screens (gallery + camera).
            @Override
            public boolean onShowFileChooser(WebView webView,
                                             ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (filePathCallback != null) filePathCallback.onReceiveValue(null);
                filePathCallback = callback;
                openFileChooser();
                return true;
            }
            // Allow the CRM to use the device location.
            @Override
            public void onGeolocationPermissionsShowPrompt(String origin,
                                                           GeolocationPermissions.Callback cb) {
                cb.invoke(origin, true, false);
            }
            // Allow getUserMedia (camera/mic) when the site is served over HTTPS.
            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                runOnUiThread(() -> request.grant(request.getResources()));
            }
        });

        // Pull-to-refresh reloads the current page.
        swipe.setOnRefreshListener(() -> web.reload());

        // Hidden settings affordance: long-press the top-left corner to change
        // the server URL (e.g. switch to an HTTPS domain later) — no reinstall.
        findViewById(R.id.settings_hotspot).setOnLongClickListener(v -> {
            showUrlDialog();
            return true;
        });

        requestRuntimePermissions();

        if (savedInstanceState != null) {
            web.restoreState(savedInstanceState);
        } else {
            web.loadUrl(getServerUrl());
        }
    }

    private String getServerUrl() {
        SharedPreferences p = getSharedPreferences(PREFS, MODE_PRIVATE);
        return p.getString(KEY_URL, DEFAULT_URL);
    }

    private void showUrlDialog() {
        final EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(getServerUrl());
        new AlertDialog.Builder(this)
                .setTitle(R.string.server_url)
                .setView(input)
                .setPositiveButton(R.string.save, (d, w) -> {
                    String url = input.getText().toString().trim();
                    if (!url.isEmpty()) {
                        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                                .putString(KEY_URL, url).apply();
                        web.loadUrl(url);
                    }
                })
                .setNegativeButton(R.string.cancel, null)
                .show();
    }

    private void requestRuntimePermissions() {
        String[] perms;
        if (Build.VERSION.SDK_INT >= 33) {
            perms = new String[]{Manifest.permission.CAMERA,
                    Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.READ_MEDIA_IMAGES};
        } else {
            perms = new String[]{Manifest.permission.CAMERA,
                    Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.READ_EXTERNAL_STORAGE};
        }
        boolean need = false;
        for (String p : perms) {
            if (ContextCompat.checkSelfPermission(this, p) != PackageManager.PERMISSION_GRANTED) {
                need = true; break;
            }
        }
        if (need) ActivityCompat.requestPermissions(this, perms, REQ_PERMS);
    }

    // ---- file chooser (gallery + camera) ----
    private void openFileChooser() {
        Intent gallery = new Intent(Intent.ACTION_GET_CONTENT);
        gallery.addCategory(Intent.CATEGORY_OPENABLE);
        gallery.setType("*/*");
        gallery.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{"image/*", "application/pdf",
                "application/vnd.ms-excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"});

        Intent camera = null;
        try {
            File photo = File.createTempFile("photo_", ".jpg", getExternalCacheDir());
            cameraImageUri = FileProvider.getUriForFile(this,
                    getPackageName() + ".fileprovider", photo);
            camera = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
            camera.putExtra(MediaStore.EXTRA_OUTPUT, cameraImageUri);
            camera.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        } catch (Exception e) {
            cameraImageUri = null;
        }

        Intent chooser = Intent.createChooser(gallery, getString(R.string.choose));
        if (camera != null) {
            chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, new Intent[]{camera});
        }
        startActivityForResult(chooser, REQ_PERMS + 1);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQ_PERMS + 1) return;
        if (filePathCallback == null) return;
        Uri[] results = null;
        if (resultCode == Activity.RESULT_OK) {
            if (data != null && data.getData() != null) {
                results = new Uri[]{data.getData()};          // picked from gallery/files
            } else if (cameraImageUri != null) {
                results = new Uri[]{cameraImageUri};            // captured with camera
            }
        }
        filePathCallback.onReceiveValue(results);
        filePathCallback = null;
        cameraImageUri = null;
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions,
                                           @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        // No-op: permissions are optional; features degrade gracefully if denied.
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onSaveInstanceState(@NonNull Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }
}
