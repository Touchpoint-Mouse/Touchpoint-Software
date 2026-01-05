# -*- coding: utf-8 -*-
# Dependency checker for Touchpoint NVDA addon
# Based on AI-content-describer's approach

import sys
import os
import threading
import urllib.request
import urllib.error
import zipfile
import tempfile
import wx

import addonHandler
import logHandler
import gui
import globalVars
import core

log = logHandler.log

try:
    addonHandler.initTranslation()
except addonHandler.AddonError:
    log.warning("Couldn't initialise translations.")


class DownloadProgressDialog(wx.Dialog):
    """Dialog showing download progress."""
    
    def __init__(self, parent, title, url):
        super().__init__(parent, title=title)
        self.url = url
        self.download = None
        self.download_canceled = False
        self.error = None
        
        mainSizer = wx.BoxSizer(wx.VERTICAL)
        
        self.progressLabel = wx.StaticText(self, label="Downloading dependencies...")
        mainSizer.Add(self.progressLabel, flag=wx.ALL, border=10)
        
        self.progressGauge = wx.Gauge(self, range=100)
        mainSizer.Add(self.progressGauge, flag=wx.ALL | wx.EXPAND, border=10)
        
        btnSizer = wx.StdDialogButtonSizer()
        self.cancelBtn = wx.Button(self, wx.ID_CANCEL)
        btnSizer.AddButton(self.cancelBtn)
        btnSizer.Realize()
        mainSizer.Add(btnSizer, flag=wx.ALL | wx.ALIGN_RIGHT, border=10)
        
        self.SetSizerAndFit(mainSizer)
        self.CenterOnScreen()
        
        self.Bind(wx.EVT_BUTTON, self.on_cancel, id=wx.ID_CANCEL)
    
    def on_cancel(self, event):
        """Cancel the download."""
        self.download_canceled = True
        self.EndModal(wx.ID_CANCEL)
    
    def start_download(self):
        """Start downloading in a background thread."""
        self.download_thread = threading.Thread(target=self._download_worker, daemon=True)
        self.download_thread.start()
    
    def _download_worker(self):
        """Worker thread that performs the download."""
        try:
            temp_file = tempfile.mktemp(suffix=".zip")
            
            def report_hook(block_num, block_size, total_size):
                """Report download progress."""
                if self.download_canceled:
                    raise Exception("Download canceled")
                
                if total_size > 0:
                    percent = min(int(block_num * block_size * 100 / total_size), 100)
                    wx.CallAfter(self.progressGauge.SetValue, percent)
                    wx.CallAfter(self.progressLabel.SetLabel, 
                               f"Downloading dependencies... {percent}%")
            
            urllib.request.urlretrieve(self.url, temp_file, report_hook)
            
            self.download = (temp_file,)
            wx.CallAfter(self.EndModal, wx.ID_OK)
            
        except Exception as e:
            self.error = str(e)
            log.error(f"Download failed: {e}")
            wx.CallAfter(self.EndModal, wx.ID_ABORT)


def show_modal(dlg):
    """Show a modal dialog."""
    return dlg.ShowModal()


def show_question(parent, title, message):
    """Show a yes/no question dialog."""
    dlg = wx.MessageDialog(parent, message, title, wx.YES_NO | wx.ICON_QUESTION)
    result = dlg.ShowModal()
    dlg.Destroy()
    return result == wx.ID_YES


def show_error(parent, title, message):
    """Show an error dialog."""
    dlg = wx.MessageDialog(parent, message, title, wx.OK | wx.ICON_ERROR)
    dlg.ShowModal()
    dlg.Destroy()


def show_information(parent, title, message):
    """Show an information dialog."""
    dlg = wx.MessageDialog(parent, message, title, wx.OK | wx.ICON_INFORMATION)
    dlg.ShowModal()
    dlg.Destroy()


def prompt_not_found():
    """Prompt user when dependencies are not found."""
    return show_question(
        None,
        "Touchpoint",
        "The Touchpoint addon requires additional dependencies (numpy, dxcam, opencv-python) "
        "that are not installed. Would you like to download and install them now?\n\n"
        "NVDA will need to be restarted after installation."
    )


def prompt_restart():
    """Prompt user to restart NVDA."""
    show_information(
        None,
        "Touchpoint",
        "Dependencies have been installed successfully. Please restart NVDA for the changes to take effect."
    )


def dependencies_not_available():
    """Show error when dependencies cannot be downloaded."""
    show_error(
        None,
        "Touchpoint",
        "The required dependencies could not be downloaded. Please check your internet connection "
        "or install them manually using the NVDA Python console:\n\n"
        "import subprocess, sys\n"
        "subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'numpy', 'dxcam', 'opencv-python'])"
    )


def download_failed(error):
    """Show error when download fails."""
    show_error(
        None,
        "Touchpoint",
        f"Failed to download dependencies: {error}\n\n"
        "You can install them manually using the NVDA Python console."
    )


def get_dependencies_path():
    """Get the path where dependencies should be installed."""
    base = globalVars.appArgs.configPath
    py_version = sys.version_info
    path = os.path.abspath(os.path.join(base, f"touchpoint-deps-py{py_version.major}.{py_version.minor}"))
    return path


def get_dependencies_url():
    """Get the URL to download dependencies from.
    
    For development, you'll need to create a GitHub release with a pre-built
    dependencies package. For now, return None to indicate manual installation needed.
    """
    # TODO: Replace with actual GitHub release URL once you create it
    # For example: "https://github.com/YOUR_USERNAME/YOUR_REPO/releases/download/deps-release/touchpoint-deps-py3.11.zip"
    return None


def unzip_and_move_dependencies(downloaded_file, destination):
    """Extract downloaded dependencies to destination."""
    if not os.path.isdir(destination):
        os.makedirs(destination)
    
    with zipfile.ZipFile(downloaded_file, "r") as zf:
        zf.extractall(destination)
    
    return True


def expand_path():
    """Add dependencies path to sys.path."""
    path = get_dependencies_path()
    if path not in sys.path:
        sys.path.insert(0, path)  # Insert at beginning for priority
    return path


def collapse_path():
    """Remove dependencies path from sys.path."""
    path = get_dependencies_path()
    if path in sys.path:
        sys.path.remove(path)


def check_dependencies():
    """Check if dependencies are available and prompt for installation if not."""
    # First check if dependencies are already importable
    try:
        import numpy
        import dxcam
        return True
    except ImportError:
        pass
    
    # Check if dependencies are in our custom path
    deps_path = get_dependencies_path()
    if os.path.isdir(deps_path) and len(os.listdir(deps_path)) > 0:
        expand_path()
        try:
            import numpy
            import dxcam
            return True
        except ImportError:
            pass
    
    # Dependencies not found, prompt user
    if not prompt_not_found():
        return False
    
    # Check if we have a download URL configured
    url = get_dependencies_url()
    if url is None:
        # No pre-built package available, suggest manual installation
        show_information(
            None,
            "Touchpoint",
            "For development testing, install dependencies using the NVDA Python console (NVDA+Control+Z):\n\n"
            "import subprocess, sys\n"
            "subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'numpy', 'dxcam', 'opencv-python'])\n\n"
            "After installation, restart NVDA."
        )
        return False
    
    # Download dependencies
    dlg = DownloadProgressDialog(None, "Touchpoint", url)
    dlg.start_download()
    result = show_modal(dlg)
    
    if result == wx.ID_OK:
        if dlg.download_canceled:
            return False
        
        # Extract dependencies
        result = unzip_and_move_dependencies(dlg.download[0], deps_path)
        if result:
            os.remove(dlg.download[0])
            expand_path()
            prompt_restart()
            return True
    elif result == wx.ID_ABORT:
        download_failed(dlg.error)
        return False
    
    return False


def check_versions_async():
    """Check dependencies asynchronously on startup."""
    # Only check on first import, not every time
    if hasattr(check_versions_async, '_checked'):
        return
    check_versions_async._checked = True
    
    # Check in background to avoid blocking NVDA startup
    def worker():
        try:
            check_dependencies()
        except Exception as e:
            log.error(f"Error checking dependencies: {e}")
    
    threading.Thread(target=worker, daemon=True).start()


# Run check when module is imported
if wx.GetApp():  # Only if GUI is available
    wx.CallAfter(check_versions_async)
