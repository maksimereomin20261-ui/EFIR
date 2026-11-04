import importlib.util, sys, types, unittest, urllib.error
from pathlib import Path
from unittest.mock import patch
try:
    import tkinter
except ModuleNotFoundError:
    tk=types.ModuleType('tkinter'); tk.messagebox=types.ModuleType('tkinter.messagebox')
    sys.modules['tkinter']=tk;sys.modules['tkinter.messagebox']=tk.messagebox
spec=importlib.util.spec_from_file_location('launcher_rate_test',Path(__file__).with_name('launcher.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class RateLimitTests(unittest.TestCase):
    def test_api_limits_and_server_errors_use_website(self):
        expected=('v1','https://github.com/file','a'*64,123)
        for code in (403,429,500):
            with self.subTest(code=code), patch.object(m,'release_metadata_api',side_effect=urllib.error.HTTPError('',code,'error',{},None)), patch.object(m,'release_metadata_website',return_value=expected) as fallback:
                self.assertEqual(m.release_metadata(),expected)
                fallback.assert_called_once()
    def test_other_http_error_does_not_hide_failure(self):
        with patch.object(m,'release_metadata_api',side_effect=urllib.error.HTTPError('',404,'missing',{},None)), patch.object(m,'release_metadata_website') as fallback:
            with self.assertRaises(urllib.error.HTTPError):m.release_metadata()
            fallback.assert_not_called()
    def test_exact_asset_and_digest_parsed(self):
        p=m.ReleaseAssetParser('/repo/releases/download/v1/Efir-App.zip')
        p.feed('<a href="/repo/releases/download/v1/Efir-App.zip">app</a><clipboard-copy aria-label="Copy to clipboard digest for Efir-App.zip" value="sha256:'+'a'*64+'"></clipboard-copy>')
        self.assertEqual(len(p.links),1);self.assertEqual(p.digests,['sha256:'+'a'*64])
    def test_rejects_missing_or_invalid_digest(self):
        class Response:
            def __init__(self, url='', body=b''):self.url=url;self.body=body;self.headers={'Content-Length':'3'}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def geturl(self):return self.url
            def read(self,*args):return self.body
        tag='v1'; path=f'/{m.REPO}/releases/download/{tag}/{m.ASSET}'
        for value in ('', 'sha256:abc'):
            html=f'<a href="{path}">a</a><clipboard-copy aria-label="Copy to clipboard digest for {m.ASSET}" value="{value}"></clipboard-copy>'.encode()
            calls=[Response(f'https://github.com/{m.REPO}/releases/tag/{tag}'),Response(body=html)]
            with self.subTest(value=value), patch.object(m.urllib.request,'urlopen',side_effect=calls):
                with self.assertRaises(ValueError):m.release_metadata_website()
if __name__=='__main__':unittest.main(verbosity=2)
