package cn.flyyye.petpark;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.view.KeyEvent;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import androidx.annotation.Nullable;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

public class MainActivity extends AppCompatActivity {

    private static final String HOME_URL = "https://bot.flyyye.cn/";
    private static final String VERSION_URL = "https://bot.flyyye.cn/api/app/version";
    private static final int FILE_CHOOSER_CODE = 10001;

    private WebView webView;
    private SwipeRefreshLayout swipe;
    private ValueCallback<Uri[]> filePathCallback;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        swipe = findViewById(R.id.swipe);
        webView = findViewById(R.id.webview);

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setSupportZoom(false);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String scheme = uri.getScheme() == null ? "" : uri.getScheme();
                if (!scheme.equals("http") && !scheme.equals("https")) {
                    try {
                        startActivity(new Intent(Intent.ACTION_VIEW, uri));
                    } catch (ActivityNotFoundException ignored) {
                    }
                    return true;
                }
                String host = uri.getHost() == null ? "" : uri.getHost();
                if (host.endsWith("flyyye.cn") || host.endsWith("qlogo.cn")) {
                    return false;
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));
                } catch (ActivityNotFoundException ignored) {
                }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                swipe.setRefreshing(false);
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (filePathCallback != null) {
                    filePathCallback.onReceiveValue(null);
                }
                filePathCallback = callback;
                try {
                    startActivityForResult(params.createIntent(), FILE_CHOOSER_CODE);
                } catch (ActivityNotFoundException e) {
                    filePathCallback = null;
                    Toast.makeText(MainActivity.this, "无法打开文件选择器", Toast.LENGTH_SHORT).show();
                    return false;
                }
                return true;
            }
        });

        webView.setDownloadListener((url, userAgent, contentDisposition, mimetype, contentLength) -> {
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
            } catch (ActivityNotFoundException ignored) {
            }
        });

        swipe.setOnRefreshListener(() -> webView.reload());
        // 页面内部滚动时禁用下拉刷新，避免误触
        swipe.setOnChildScrollUpCallback((parent, child) -> webView.getScrollY() > 0);

        webView.loadUrl(HOME_URL);
        checkUpdate();
    }

    // --------------------------- 远程更新 ---------------------------
    private void checkUpdate() {
        new Thread(() -> {
            try {
                HttpURLConnection conn = (HttpURLConnection) new URL(VERSION_URL).openConnection();
                conn.setConnectTimeout(8000);
                conn.setReadTimeout(8000);
                if (conn.getResponseCode() != 200) return;
                StringBuilder sb = new StringBuilder();
                try (BufferedReader r = new BufferedReader(new InputStreamReader(conn.getInputStream()))) {
                    String line;
                    while ((line = r.readLine()) != null) sb.append(line);
                }
                JSONObject o = new JSONObject(sb.toString());
                if (!o.optBoolean("ok")) return;
                int remote = o.optInt("version_code", 0);
                if (remote <= BuildConfig.VERSION_CODE) return;
                String name = o.optString("version_name", "");
                String changelog = o.optString("changelog", "");
                String url = o.optString("url", "");
                if (url.isEmpty()) return;
                final String fullUrl = url.startsWith("http") ? url : HOME_URL.substring(0, HOME_URL.length() - 1) + url;
                runOnUiThread(() -> showUpdateDialog(name, changelog, fullUrl));
            } catch (Exception ignored) {
            }
        }).start();
    }

    private void showUpdateDialog(String versionName, String changelog, String url) {
        if (isFinishing()) return;
        String msg = "发现新版本 " + versionName + (changelog.isEmpty() ? "" : "\n\n更新内容：\n" + changelog);
        new AlertDialog.Builder(this)
                .setTitle("应用更新")
                .setMessage(msg)
                .setPositiveButton("立即更新", (d, w) -> {
                    try {
                        startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
                    } catch (ActivityNotFoundException ignored) {
                    }
                })
                .setNegativeButton("稍后再说", null)
                .show();
    }

    // --------------------------- 文件选择回调 ---------------------------
    @Override
    protected void onActivityResult(int requestCode, int resultCode, @Nullable Intent data) {
        if (requestCode == FILE_CHOOSER_CODE) {
            if (filePathCallback != null) {
                filePathCallback.onReceiveValue(
                        WebChromeClient.FileChooserParams.parseResult(resultCode, data));
                filePathCallback = null;
            }
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    // --------------------------- 返回键 ---------------------------
    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && webView.canGoBack()) {
            webView.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }
}
