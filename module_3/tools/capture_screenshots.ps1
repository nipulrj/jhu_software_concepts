# Capture the three screenshots the assignment asks for:
#
#   01_raw_sql_output_q1-6.png    query_data.py   (raw SQL via psycopg)
#   02_raw_sql_output_q7-11.png
#   03_orm_output_q1-6.png        orm_queries.py  (SQLAlchemy ORM)
#   04_orm_output_q7-11.png
#   05_orm_vs_sql_compare.png     the two engines cross-checked
#   06_flask_page.png             the running Flask page
#
# Each is a real screen capture of a real window, not a rendering of captured
# text. The script launches each window itself, brings it to the front and
# captures only that window's rectangle -- never the whole desktop, so nothing
# else that happens to be on screen is included.
#
#     powershell -ExecutionPolicy Bypass -File tools\capture_screenshots.ps1
#
# Requires the Flask app to be running already (python app.py) for the third.

param(
    [string]$Url = "http://127.0.0.1:5000",
    [int]$ConsoleWidth = 130,
    # Pixel size of the console window. Sized so a six-question batch fits on one
    # screen with room to spare; check against your display before raising it.
    [int]$WindowPixelWidth = 1180,
    [int]$WindowPixelHeight = 1150,
    # How long the launched shell waits before printing. The window has to be
    # resized first: output printed into a short viewport stays where it was
    # written, and Windows Terminal does not reflow it when the window grows.
    [int]$StartupDelaySeconds = 4
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

# GetWindowRect so we capture one window rather than the full screen;
# SetForegroundWindow + ShowWindow so the window is actually visible when we do.
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public class Win {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll")] public static extern bool MoveWindow(
        IntPtr h, int x, int y, int w, int t, bool repaint);

    public delegate bool EnumProc(IntPtr h, IntPtr l);

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }

    // Windows 11 hosts powershell.exe inside Windows Terminal, so the process we
    // start owns no window of its own and Process.MainWindowHandle is zero.
    // Searching every visible window for a title we set ourselves finds it
    // whichever host ends up owning it.
    public static IntPtr FindByTitle(string needle) {
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate(IntPtr h, IntPtr l) {
            if (!IsWindowVisible(h)) return true;
            StringBuilder sb = new StringBuilder(512);
            GetWindowText(h, sb, sb.Capacity);
            if (sb.ToString().Contains(needle)) { found = h; return false; }
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
"@

$root = Split-Path -Parent $PSScriptRoot
$shots = Join-Path $root "screenshots"
New-Item -ItemType Directory -Force -Path $shots | Out-Null

function Save-WindowShot {
    param([IntPtr]$Handle, [string]$Path)

    [void][Win]::ShowWindow($Handle, 9)      # SW_RESTORE
    [void][Win]::SetForegroundWindow($Handle)
    Start-Sleep -Milliseconds 900            # let it paint before capturing

    $rect = New-Object Win+RECT
    if (-not [Win]::GetWindowRect($Handle, [ref]$rect)) {
        throw "Could not read the window rectangle."
    }

    $width = $rect.Right - $rect.Left
    $height = $rect.Bottom - $rect.Top
    if ($width -le 0 -or $height -le 0) { throw "Window has no size yet." }

    $bitmap = New-Object System.Drawing.Bitmap $width, $height
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
    $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose()
    $bitmap.Dispose()

    Write-Output ("wrote {0} ({1}x{2})" -f (Split-Path -Leaf $Path), $width, $height)
}

function Wait-ForWindow {
    param([string]$Title, [int]$TimeoutSeconds = 20)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $handle = [Win]::FindByTitle($Title)
        if ($handle -ne [IntPtr]::Zero) { return $handle }
        Start-Sleep -Milliseconds 400
    }
    throw "No window titled '*$Title*' appeared within $TimeoutSeconds seconds."
}

function Capture-Console {
    # -Height is per capture: the batches differ a lot in length, and one size
    # for all of them either clips the long ones or leaves the short ones
    # mostly empty.
    param(
        [string]$Script,
        [string]$Path,
        [int]$Height = $WindowPixelHeight,
        [int]$SettleSeconds = 6
    )

    # A marker unique to this run, so we capture our own window and never one the
    # user happens to have open.
    $marker = "M3CAPTURE-" + [guid]::NewGuid().ToString("N").Substring(0, 8)

    # -NoExit keeps the finished output on screen so there is something to
    # capture; the window is closed again once the shot is taken.
    $command = @"
`$Host.UI.RawUI.WindowTitle = '$marker'
`$b = `$Host.UI.RawUI.BufferSize
try { `$b.Width = $ConsoleWidth; `$b.Height = 3000; `$Host.UI.RawUI.BufferSize = `$b } catch { }
Set-Location '$root'
Start-Sleep -Seconds $StartupDelaySeconds
Clear-Host
$Script
"@

    $process = Start-Process powershell `
        -ArgumentList "-NoExit", "-NoProfile", "-Command", $command `
        -PassThru
    try {
        $handle = Wait-ForWindow -Title $marker

        # Windows Terminal ignores $Host.UI.RawUI.WindowSize -- that is the legacy
        # console API, and a modern terminal only honours it under conhost. Sizing
        # the window itself in pixels does work, and the terminal reflows its rows
        # to match, which is what actually makes the whole answer fit on screen.
        [void][Win]::MoveWindow($handle, 30, 20, $WindowPixelWidth, $Height, $true)

        # Wait out the shell's startup delay, then give the command time to run.
        Start-Sleep -Seconds ($StartupDelaySeconds + $SettleSeconds)
        Save-WindowShot -Handle $handle -Path $Path
    }
    finally {
        if (-not $process.HasExited) { $process.Kill() }
    }
}

# The full eleven-question run is far longer than a console window, and Windows
# Terminal ignores a scroll key sent with SendKeys, so a single window can only
# ever show the tail. The --questions selector splits the run into two batches
# that each fit on screen, which between them show every answer in full.
Write-Output "Capturing raw SQL output..."
Capture-Console -Script "python -X utf8 query_data.py --questions 1-6" `
                -Path (Join-Path $shots "01_raw_sql_output_q1-6.png") -Height 790
Capture-Console -Script "python -X utf8 query_data.py --questions 7-11" `
                -Path (Join-Path $shots "02_raw_sql_output_q7-11.png") -Height 1270

Write-Output "Capturing SQLAlchemy ORM output..."
Capture-Console -Script "python -X utf8 orm_queries.py --questions 1-6" `
                -Path (Join-Path $shots "03_orm_output_q1-6.png") -Height 790
Capture-Console -Script "python -X utf8 orm_queries.py --questions 7-11" `
                -Path (Join-Path $shots "04_orm_output_q7-11.png") -Height 1270

Write-Output "Capturing the ORM-versus-SQL cross-check..."
Capture-Console -Script "python -X utf8 orm_queries.py --compare" `
                -Path (Join-Path $shots "05_orm_vs_sql_compare.png") -Height 400 -SettleSeconds 12

Write-Output "Capturing the Flask page..."
# --app gives a single clean window with no tab strip or address bar, and
# --user-data-dir keeps it out of any signed-in profile.
$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edge)) { $edge = "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe" }
if (-not (Test-Path $edge)) { throw "Could not find msedge.exe; capture the page by hand." }

# --inprivate matters for more than tidiness. On a first run Edge signs itself
# in with the Windows account and shows a "we are now syncing your browsing
# data" dialog, which both covers the page and prints the account's email
# address onto the screenshot -- and these screenshots are committed and
# submitted. An InPrivate window never signs in, so the dialog cannot appear.
$profileDir = Join-Path $env:TEMP "m3-shot-profile"
$browser = Start-Process $edge -PassThru -ArgumentList @(
    "--app=$Url",
    "--inprivate",
    "--window-size=1280,1000",
    "--window-position=40,40",
    "--user-data-dir=$profileDir",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
    "--disable-features=Translate,msImplicitSignin,EdgeImplicitSignIn"
)
try {
    Start-Sleep -Seconds 8
    $browser.Refresh()
    $handle = $browser.MainWindowHandle
    if ($handle -eq [IntPtr]::Zero) {
        # Edge hands the window to an existing broker process, so the one we
        # started may own nothing; find whichever msedge process has a window.
        $owner = Get-Process msedge -ErrorAction SilentlyContinue |
                 Where-Object { $_.MainWindowHandle -ne 0 } |
                 Sort-Object StartTime -Descending | Select-Object -First 1
        if (-not $owner) { throw "No Edge window found to capture." }
        $handle = $owner.MainWindowHandle
    }
    Save-WindowShot -Handle $handle -Path (Join-Path $shots "06_flask_page.png")
}
finally {
    Get-Process msedge -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $edge -and $_.CommandLine -like "*m3-shot-profile*" } |
        ForEach-Object { try { $_.CloseMainWindow() | Out-Null } catch {} }
    if ($browser -and -not $browser.HasExited) { try { $browser.Kill() } catch {} }
}

Write-Output "Done. Screenshots are in $shots"
