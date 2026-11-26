param(
    [Parameter(Mandatory)][int]$AppPid,
    [Parameter(Mandatory)][string]$Fixture
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class NativePickerInput
{
    private const uint WM_SETTEXT = 0x000C;
    private const uint WM_COMMAND = 0x0111;
    private const uint SMTO_ABORTIFHUNG = 0x0002;
    private const uint IDOK = 1;

    [DllImport("user32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern IntPtr SendMessageTimeoutW(
        IntPtr window, uint message, UIntPtr wParam, string lParam,
        uint flags, uint timeout, out UIntPtr result);

    [DllImport("user32.dll", ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool PostMessageW(IntPtr window, uint message, UIntPtr wParam, IntPtr lParam);

    public static void Choose(IntPtr dialog, IntPtr edit, IntPtr open, string filename)
    {
        UIntPtr result;
        if (SendMessageTimeoutW(edit, WM_SETTEXT, UIntPtr.Zero, filename,
                                SMTO_ABORTIFHUNG, 5000, out result) == IntPtr.Zero || result == UIntPtr.Zero)
            throw new InvalidOperationException("The native file name box rejected the fixture path.");

        // BN_CLICKED is zero in the high word. The low word identifies the Open button.
        if (!PostMessageW(dialog, WM_COMMAND, new UIntPtr(IDOK), open))
            throw new InvalidOperationException("The native file dialog rejected the Open command.");
    }
}
'@

function Find-Element($Root, $Property, $Value, $Scope = [System.Windows.Automation.TreeScope]::Descendants) {
    $condition = New-Object System.Windows.Automation.PropertyCondition($Property, $Value)
    $Root.FindFirst($Scope, $condition)
}

function Wait-Element([string]$Description, [scriptblock]$Find) {
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        $element = & $Find
        if ($element) { return $element }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Could not locate $Description through Windows UI Automation."
}

$app = Get-Process -Id $AppPid
$name = [System.Windows.Automation.AutomationElement]::NameProperty
$id = [System.Windows.Automation.AutomationElement]::AutomationIdProperty
$descendants = [System.Windows.Automation.TreeScope]::Descendants
$window = [System.Windows.Automation.AutomationElement]::FromHandle($app.MainWindowHandle)
$import = Wait-Element 'the empty workspace import button' { Find-Element $window $name 'import footage' }
$import.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()

$desktop = [System.Windows.Automation.AutomationElement]::RootElement
$dialog = Wait-Element 'the native media picker' {
    Find-Element $desktop $name 'Select original media' ([System.Windows.Automation.TreeScope]::Children)
}
if ($dialog.Current.ControlType -ne [System.Windows.Automation.ControlType]::Window -or $dialog.Current.ProcessId -ne $AppPid) {
    throw 'The media picker did not open as a native window owned by ArcherGate.'
}
# The Windows runner exposes both dialog controls as panes without Value or Invoke patterns.
# Identify the HWNDs inside the owned dialog by their native classes and IDs.
$filename = Wait-Element 'the native file name field' {
    $filenameId = [System.Windows.Automation.PropertyCondition]::new($id, '1148')
    foreach ($candidate in $dialog.FindAll($descendants, $filenameId)) {
        if ($candidate.Current.ClassName -eq 'Edit' -and $candidate.Current.NativeWindowHandle -ne 0) {
            return $candidate
        }
    }
}
$open = Wait-Element 'the native Open button' {
    $button = Find-Element $dialog $id '1'
    if ($button -and $button.Current.ClassName -eq 'Button' -and $button.Current.NativeWindowHandle -ne 0) { $button }
}
if ($dialog.Current.NativeWindowHandle -eq 0) { throw 'The native picker has no window handle.' }
[NativePickerInput]::Choose([IntPtr]$dialog.Current.NativeWindowHandle,
    [IntPtr]$filename.Current.NativeWindowHandle, [IntPtr]$open.Current.NativeWindowHandle, $Fixture)
Write-Host 'Submitted the fixture through the native file dialog.'

$label = '1 file ready ' + [char]0x00B7 + ' ' + (Split-Path -Leaf $Fixture)
$null = Wait-Element 'the selected filename in the WebView2 import form' { Find-Element $window $name $label }
Write-Host 'The Windows picker opened a real synthetic media path and returned it to the import form.'
