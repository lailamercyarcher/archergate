param(
    [Parameter(Mandatory)][int]$AppPid,
    [Parameter(Mandatory)][string]$Fixture
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

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
$class = [System.Windows.Automation.AutomationElement]::ClassNameProperty
$type = [System.Windows.Automation.AutomationElement]::ControlTypeProperty
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
$edit = [System.Windows.Automation.ControlType]::Edit
$valuePattern = [System.Windows.Automation.ValuePattern]::Pattern
# Windows can expose the filename as an edit or as a control hosted by the shell's combo box.
$filenameCondition = [System.Windows.Automation.OrCondition]::new([System.Windows.Automation.Condition[]]@(
    [System.Windows.Automation.PropertyCondition]::new($id, '1148')
    [System.Windows.Automation.PropertyCondition]::new($name, 'File name:')
    [System.Windows.Automation.PropertyCondition]::new($class, 'FileNameControlHost')
))
try {
    $filenameValue = Wait-Element 'the native file name field' {
        foreach ($candidate in $dialog.FindAll($descendants, $filenameCondition)) {
            $control = $candidate
            if ($control.Current.ControlType -ne $edit) {
                $child = Find-Element $candidate $type $edit
                if ($child) { $control = $child }
            }
            if ($control.Current.IsEnabled -and -not $control.Current.IsOffscreen) {
                $pattern = $null
                if ($control.TryGetCurrentPattern($valuePattern, [ref]$pattern) -and -not $pattern.Current.IsReadOnly) {
                    return $pattern
                }
            }
        }
    }
} catch {
    Write-Host 'Native picker automation elements:'
    foreach ($element in $dialog.FindAll($descendants, [System.Windows.Automation.Condition]::TrueCondition)) {
        $current = $element.Current
        if ($current.Name -or $current.AutomationId -or $current.ClassName) {
            Write-Host ('{0} id={1} class={2} name={3}' -f $current.ControlType.ProgrammaticName,
                $current.AutomationId, $current.ClassName, $current.Name)
        }
    }
    throw
}
$filenameValue.SetValue($Fixture)
$open = Wait-Element 'the native Open button' { Find-Element $dialog $id '1' }
$open.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()

$label = '1 file ready ' + [char]0x00B7 + ' ' + (Split-Path -Leaf $Fixture)
$null = Wait-Element 'the selected filename in the WebView2 import form' { Find-Element $window $name $label }
Write-Host 'The Windows picker opened a real synthetic media path and returned it to the import form.'
