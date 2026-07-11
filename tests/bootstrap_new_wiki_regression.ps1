param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Bootstrap = Join-Path $RepoRoot 'scripts\bootstrap_new_wiki.ps1'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
  'paper-wiki-bootstrap-test-' + [Guid]::NewGuid().ToString('N'))

function Assert([bool]$Condition, [string]$Message) {
  if (-not $Condition) { throw $Message }
}

function WriteText([string]$Path, [string]$Text) {
  $parent = [System.IO.Path]::GetDirectoryName($Path)
  if ($parent) { [System.IO.Directory]::CreateDirectory($parent) | Out-Null }
  [System.IO.File]::WriteAllText($Path, $Text, $Utf8NoBom)
}

function InvokeBootstrap([string[]]$Arguments) {
  $oldPreference = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Bootstrap @Arguments 2>&1 |
      Out-String
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $oldPreference
  }
  return [pscustomobject]@{ Code = $code; Output = $output }
}

function NewCurrentProject([string]$Path, [string]$Topic, [string]$Variant) {
  $result = InvokeBootstrap @(
    '-NewPath', $Path, '-Topic', $Topic, '-ProjectName', 'Regression Project',
    '-Variant', $Variant)
  Assert ($result.Code -eq 0) "create failed ($Variant): $($result.Output)"
}

try {
  [System.IO.Directory]::CreateDirectory($TempRoot) | Out-Null

  $research = Join-Path $TempRoot 'Research Project With Spaces'
  NewCurrentProject $research 'regression-research' 'research'
  $wikiBefore = [System.IO.File]::ReadAllText((Join-Path $research 'WIKI.md'), $Utf8NoBom)
  $result = InvokeBootstrap @('-NewPath', $research, '-Update')
  Assert ($result.Code -eq 0) "research update failed: $($result.Output)"
  Assert ([System.IO.File]::ReadAllText((Join-Path $research 'WIKI.md'), $Utf8NoBom) -eq $wikiBefore) `
    'research update did not preserve WIKI.md'

  $course = Join-Path $TempRoot 'Course Project'
  NewCurrentProject $course 'regression-course' 'course'
  $result = InvokeBootstrap @('-NewPath', $course, '-Update')
  Assert ($result.Code -eq 0) "course update failed: $($result.Output)"

  $missingWiki = Join-Path $TempRoot 'Missing Wiki'
  NewCurrentProject $missingWiki 'missing-wiki' 'research'
  [System.IO.File]::Delete((Join-Path $missingWiki 'WIKI.md'))
  $result = InvokeBootstrap @('-NewPath', $missingWiki, '-Update')
  Assert ($result.Code -eq 3) "managed project without WIKI.md returned $($result.Code)"
  Assert (-not [System.IO.File]::Exists((Join-Path $missingWiki 'WIKI.md'))) `
    'managed project reconstructed WIKI.md from CLAUDE.md'

  $missingBoth = Join-Path $TempRoot 'Missing Both'
  NewCurrentProject $missingBoth 'missing-both' 'research'
  [System.IO.File]::Delete((Join-Path $missingBoth 'WIKI.md'))
  [System.IO.File]::Delete((Join-Path $missingBoth 'CLAUDE.md'))
  $result = InvokeBootstrap @('-NewPath', $missingBoth, '-Update')
  Assert ($result.Code -eq 3) "managed project without adapters returned $($result.Code)"
  Assert (-not [System.IO.File]::Exists((Join-Path $missingBoth 'CLAUDE.md'))) `
    'failed update modified a managed project without WIKI.md'

  $legacy = Join-Path $TempRoot 'Legacy Project'
  $legacyRules = @(
    '# Legacy LLM Wiki',
    '<!-- paper-wiki-variant: research -->',
    'wiki/papers/',
    'CUSTOM LEGACY RULES',
    ''
  ) -join "`n"
  WriteText (Join-Path $legacy 'CLAUDE.md') $legacyRules
  WriteText (Join-Path $legacy '.claude\commands\wiki-init.md') 'legacy'
  WriteText (Join-Path $legacy '.claude\commands\wiki-compile.md') 'legacy'
  [System.IO.Directory]::CreateDirectory((Join-Path $legacy 'raw\legacy-topic')) | Out-Null
  $result = InvokeBootstrap @('-NewPath', $legacy, '-Update')
  Assert ($result.Code -eq 0) "confirmed legacy migration failed: $($result.Output)"
  Assert ([System.IO.File]::ReadAllText((Join-Path $legacy 'WIKI.md'), $Utf8NoBom) -eq $legacyRules) `
    'legacy CLAUDE.md was not copied exactly to WIKI.md'

  $thin = Join-Path $TempRoot 'Thin Adapter'
  $thinAdapter = @(
    '# Claude Code project adapter',
    '<!-- paper-wiki-variant: research -->',
    'WIKI.md is the only canonical source for project rules.',
    'paper-wiki wiki/papers/',
    ''
  ) -join "`n"
  WriteText (Join-Path $thin 'CLAUDE.md') $thinAdapter
  WriteText (Join-Path $thin '.claude\commands\wiki-init.md') 'legacy-looking'
  WriteText (Join-Path $thin '.claude\commands\wiki-compile.md') 'legacy-looking'
  [System.IO.Directory]::CreateDirectory((Join-Path $thin 'raw\thin-topic')) | Out-Null
  $result = InvokeBootstrap @('-NewPath', $thin, '-Update')
  Assert ($result.Code -eq 3) "thin adapter migration returned $($result.Code)"
  Assert (-not [System.IO.File]::Exists((Join-Path $thin 'WIKI.md'))) `
    'thin CLAUDE.md adapter was migrated to WIKI.md'

  Write-Host 'PowerShell bootstrap regression: PASS'
} finally {
  if ([System.IO.Directory]::Exists($TempRoot)) {
    $full = [System.IO.Path]::GetFullPath($TempRoot)
    $temp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if (-not $full.StartsWith($temp, [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to remove non-temporary test path: $full"
    }
    [System.IO.Directory]::Delete($full, $true)
  }
}
