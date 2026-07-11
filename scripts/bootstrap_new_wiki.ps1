<#
.SYNOPSIS
  Bootstrap or update a dual-client LLM-Wiki project.

.DESCRIPTION
  Creates a paper-wiki project that can be operated by both Claude Code and
  Codex. WIKI.md is the canonical project rulebook. CLAUDE.md, AGENTS.md and
  the project-local Codex skill are client adapters.

  This script body is intentionally ASCII-only. All non-ASCII project content
  lives in UTF-8 templates and is read or written through .NET UTF-8 APIs.

.PARAMETER NewPath      Project path.
.PARAMETER Topic        Kebab-case source id. Required when creating.
.PARAMETER ProjectName  Human-readable project name. Defaults to Topic.
.PARAMETER Variant      research | course. Defaults to research when creating.
.PARAMETER SkillRoot    paper-wiki repository root.
.PARAMETER Update       Refresh managed adapters and workflow files in-place.
#>
param(
  [Parameter(Mandatory=$true)][string]$NewPath,
  [string]$Topic,
  [string]$ProjectName,
  [ValidateSet('research','course')][string]$Variant,
  [string]$SkillRoot,
  [switch]$Update
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$variantWasExplicit = $PSBoundParameters.ContainsKey('Variant')
$topicWasExplicit = $PSBoundParameters.ContainsKey('Topic')
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function ReadUtf8([string]$Path) {
  return [System.IO.File]::ReadAllText($Path, $Utf8NoBom)
}

function WriteUtf8([string]$Path, [string]$Text) {
  $parent = [System.IO.Path]::GetDirectoryName($Path)
  if ($parent -and -not [System.IO.Directory]::Exists($parent)) {
    [System.IO.Directory]::CreateDirectory($parent) | Out-Null
  }
  [System.IO.File]::WriteAllText($Path, $Text, $Utf8NoBom)
}

function CopyFile([string]$Source, [string]$Destination) {
  $parent = [System.IO.Path]::GetDirectoryName($Destination)
  if ($parent -and -not [System.IO.Directory]::Exists($parent)) {
    [System.IO.Directory]::CreateDirectory($parent) | Out-Null
  }
  [System.IO.File]::Copy($Source, $Destination, $true)
}

function Fail([string]$Message, [int]$Code = 1) {
  [Console]::Error.WriteLine($Message)
  exit $Code
}

function EnsureFile([string]$Path) {
  if (-not [System.IO.File]::Exists($Path)) {
    Fail "Required paper-wiki file is missing: $Path"
  }
}

function ValidateTopic([string]$Value) {
  if (-not $Value -or $Value -notmatch '^[a-z0-9]+(?:-[a-z0-9]+)*$') {
    Fail 'Topic must be lower-case kebab-case (for example: my-topic).' 2
  }
}

function Render([string]$Template, [string]$Output) {
  EnsureFile $Template
  $text = ReadUtf8 $Template
  $text = $text.Replace('{{PROJECT_NAME}}', $script:ProjectName)
  $text = $text.Replace('{{TOPIC}}', $script:Topic)
  $text = $text.Replace('{{NS}}', $script:ns)
  $text = $text.Replace('{{DATE}}', $script:date)
  $text = $text.Replace('{{NEWPATH}}', $script:NewPath)
  $text = $text.Replace('{{SCAFFOLD_VERSION}}', $script:scaffoldVersion)
  WriteUtf8 $Output $text
}

function ReadManifestScalar([string]$Path, [string]$Key) {
  if (-not [System.IO.File]::Exists($Path)) { return $null }
  $pattern = '^\s*' + [Regex]::Escape($Key) + '\s*:\s*(.*?)\s*$'
  foreach ($line in [Regex]::Split((ReadUtf8 $Path), "`r?`n")) {
    $match = [Regex]::Match($line, $pattern)
    if (-not $match.Success) { continue }
    $value = $match.Groups[1].Value.Trim()
    if ($value.Length -ge 2 -and $value[0] -eq "'" -and $value[$value.Length - 1] -eq "'") {
      return $value.Substring(1, $value.Length - 2).Replace("''", "'")
    }
    if ($value.Length -ge 2 -and $value[0] -eq '"' -and $value[$value.Length - 1] -eq '"') {
      return $value.Substring(1, $value.Length - 2)
    }
    return $value
  }
  return $null
}

function InferVariantFromText([string]$Text) {
  if (-not $Text) { return $null }
  $marker = [Regex]::Match($Text, '(?im)paper-wiki-variant\s*:\s*(research|course)')
  if ($marker.Success) { return $marker.Groups[1].Value.ToLowerInvariant() }
  if ($Text -match '(?i)wiki/papers/') { return 'research' }
  if ($Text -match '(?i)wiki/lectures/') { return 'course' }
  $legacy = [Regex]::Match($Text, '(?i)paper-wiki.{0,100}\b(research|course)\b')
  if ($legacy.Success) { return $legacy.Groups[1].Value.ToLowerInvariant() }
  return $null
}

function YamlQuote([string]$Value) {
  if ($null -eq $Value) { $Value = '' }
  if ($Value -match "[`r`n]") { Fail 'Manifest values must not contain newlines.' 2 }
  return "'" + $Value.Replace("'", "''") + "'"
}

function WriteProjectManifest([string]$Path) {
  $lines = @(
    "spec: 'llm-wiki/1.1'",
    "variant: $(YamlQuote $script:Variant)",
    "topic: $(YamlQuote $script:Topic)",
    "project_name: $(YamlQuote $script:ProjectName)",
    "scaffold_version: $(YamlQuote $script:scaffoldVersion)",
    'clients:',
    '  - claude-code',
    '  - codex',
    "canonical_rules: 'WIKI.md'"
  )
  WriteUtf8 $Path (($lines -join "`n") + "`n")
}

$allManagedCommands = @(
  'wiki-init','wiki-teach','wiki-compile','wiki-search-latest','wiki-critique','wiki-ideate','wiki-ask'
)
$allManagedAgents = @('wiki-searcher','wiki-critic','wiki-ideator')
$protocolDocs = @('llm-wiki.protocol.yaml','OCR-SETUP.md','GOTCHAS.md')
$managedTreeRoots = @(
  '.claude\commands',
  '.claude\agents',
  '.agents\skills\paper-wiki-project',
  '.paper-wiki'
)

function SetVariantAssets([string]$SelectedVariant) {
  if ($SelectedVariant -eq 'research') {
    $script:cmds = @('wiki-init','wiki-teach','wiki-compile','wiki-search-latest','wiki-critique','wiki-ideate')
    $script:agents = @('wiki-searcher','wiki-critic','wiki-ideator')
  } else {
    $script:cmds = @('wiki-init','wiki-teach','wiki-compile','wiki-critique')
    $script:agents = @('wiki-critic')
  }
}

function GetManagedFileRelatives([bool]$IncludeWiki) {
  $result = @('CLAUDE.md','AGENTS.md','.agents\skills\paper-wiki-project\SKILL.md')
  foreach ($name in $allManagedCommands) { $result += ".claude\commands\$name.md" }
  foreach ($name in $allManagedAgents) { $result += ".claude\agents\$name.md" }
  foreach ($name in $protocolDocs) { $result += ".paper-wiki\docs\$name" }
  if ($IncludeWiki) { $result += 'WIKI.md' }
  $result += '.paper-wiki\project.yaml'
  return $result
}

function GetExistingItem([string]$Path) {
  return Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
}

function AssertNotReparse([string]$Path) {
  $item = GetExistingItem $Path
  if ($null -ne $item -and (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
    Fail "Refusing reparse point in managed project path: $Path"
  }
}

function AssertWithinProject([string]$Root, [string]$Target) {
  $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\','/')
  $targetFull = [System.IO.Path]::GetFullPath($Target)
  $comparison = if ($env:OS -eq 'Windows_NT') {
    [System.StringComparison]::OrdinalIgnoreCase
  } else {
    [System.StringComparison]::Ordinal
  }
  if ($targetFull.Equals($rootFull, $comparison)) { return }
  $prefix = $rootFull + [System.IO.Path]::DirectorySeparatorChar
  if (-not $targetFull.StartsWith($prefix, $comparison)) {
    Fail "Managed target escapes project root: $Target"
  }
}

function AssertPathChainSafe([string]$Root, [string]$Relative) {
  $target = Join-Path $Root $Relative
  AssertWithinProject $Root $target
  AssertNotReparse $Root
  $current = [System.IO.Path]::GetFullPath($Root)
  $parts = $Relative -split '[\\/]'
  foreach ($part in $parts) {
    if (-not $part) { continue }
    $current = Join-Path $current $part
    AssertNotReparse $current
  }
}

function AssertManagedSecurity([string]$Root, [bool]$IncludeWiki) {
  AssertNotReparse $Root
  foreach ($relative in (GetManagedFileRelatives $IncludeWiki)) {
    AssertPathChainSafe $Root $relative
  }
  foreach ($relative in $managedTreeRoots) {
    AssertPathChainSafe $Root $relative
    $tree = Join-Path $Root $relative
    $treeItem = GetExistingItem $tree
    if ($null -eq $treeItem) { continue }
    if (-not $treeItem.PSIsContainer) {
      Fail "Managed directory path is not a directory: $tree"
    }
    foreach ($item in (Get-ChildItem -LiteralPath $tree -Force -Recurse)) {
      if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail "Refusing reparse point in managed project tree: $($item.FullName)"
      }
      AssertWithinProject $Root $item.FullName
    }
  }
  foreach ($relative in (GetManagedFileRelatives $IncludeWiki)) {
    $path = Join-Path $Root $relative
    $item = GetExistingItem $path
    if ($null -ne $item -and $item.PSIsContainer) {
      Fail "Managed file target is a directory: $path"
    }
  }
}

function InitializeSecureUpdateApi {
  if ($env:OS -ne 'Windows_NT') {
    Fail 'Secure PowerShell updates require Windows handle APIs. Use bootstrap_new_wiki.sh on macOS/Linux.' 2
  }
  if ('PaperWikiNativeMethods' -as [type]) { return }
  Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

[StructLayout(LayoutKind.Sequential)]
public struct PaperWikiFileInformation {
    public uint FileAttributes;
    public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
    public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
    public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
    public uint VolumeSerialNumber;
    public uint FileSizeHigh;
    public uint FileSizeLow;
    public uint NumberOfLinks;
    public uint FileIndexHigh;
    public uint FileIndexLow;
}

public static class PaperWikiNativeMethods {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern SafeFileHandle CreateFile(
        string fileName, uint desiredAccess, uint shareMode, IntPtr securityAttributes,
        uint creationDisposition, uint flagsAndAttributes, IntPtr templateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetFileInformationByHandle(
        SafeFileHandle file, out PaperWikiFileInformation information);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool MoveFileEx(string existingName, string newName, uint flags);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool DeleteFile(string fileName);
}
'@
}

function OpenSecureNativeItem([string]$Path, [bool]$Directory, [bool]$DenyWrite) {
  $desiredAccess = if ($Directory) { [uint32]0x80 } else { [uint32]2147483648 }
  $shareMode = [uint32](1 -bor 2 -bor 4)
  if ($DenyWrite) { $shareMode = [uint32](1 -bor 4) }
  $flags = [uint32]0x00200000
  if ($Directory) { $flags = [uint32]($flags -bor 0x02000000) }
  $handle = [PaperWikiNativeMethods]::CreateFile(
    $Path, $desiredAccess, $shareMode, [IntPtr]::Zero, 3, $flags, [IntPtr]::Zero)
  if ($null -eq $handle -or $handle.IsInvalid) {
    $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    if ($null -ne $handle) { $handle.Dispose() }
    throw "Cannot securely open managed path (Win32 $errorCode): $Path"
  }
  $information = New-Object PaperWikiFileInformation
  if (-not [PaperWikiNativeMethods]::GetFileInformationByHandle($handle, [ref]$information)) {
    $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    $handle.Dispose()
    throw "Cannot inspect managed path handle (Win32 $errorCode): $Path"
  }
  $isDirectory = ($information.FileAttributes -band 0x10) -ne 0
  $isReparse = ($information.FileAttributes -band 0x400) -ne 0
  if ($isReparse -or $isDirectory -ne $Directory) {
    $handle.Dispose()
    throw "Managed path changed type or became a reparse point: $Path"
  }
  return [pscustomobject]@{
    Handle = $handle
    Information = $information
    Identity = "$($information.VolumeSerialNumber):$($information.FileIndexHigh):$($information.FileIndexLow)"
  }
}

function PinSecureDirectory([string]$Path) {
  $full = [System.IO.Path]::GetFullPath($Path)
  if ($script:pinnedDirectoryHandles.ContainsKey($full)) { return }
  $opened = OpenSecureNativeItem $full $true $false
  $script:pinnedDirectoryHandles[$full] = $opened.Handle
  $script:pinnedDirectoryIdentities[$full] = $opened.Identity
}

function UnpinSecureDirectory([string]$Path) {
  $full = [System.IO.Path]::GetFullPath($Path)
  if (-not $script:pinnedDirectoryHandles.ContainsKey($full)) { return }
  $script:pinnedDirectoryHandles[$full].Dispose()
  $script:pinnedDirectoryHandles.Remove($full)
  $script:pinnedDirectoryIdentities.Remove($full)
}

function AssertPinnedDirectory([string]$Path) {
  $full = [System.IO.Path]::GetFullPath($Path)
  if (-not $script:pinnedDirectoryIdentities.ContainsKey($full)) {
    throw "Managed directory was not pinned for secure update: $full"
  }
  $opened = OpenSecureNativeItem $full $true $false
  try {
    if ($opened.Identity -ne $script:pinnedDirectoryIdentities[$full]) {
      throw "Managed directory changed during update: $full"
    }
  } finally {
    $opened.Handle.Dispose()
  }
}

function AssertSingleLinkFile([string]$Path) {
  $opened = OpenSecureNativeItem $Path $false $false
  try {
    if ($opened.Information.NumberOfLinks -ne 1) {
      throw "Refusing hard-linked managed file (link count $($opened.Information.NumberOfLinks)): $Path"
    }
    return $opened.Identity
  } finally {
    $opened.Handle.Dispose()
  }
}

function CopyStreamToNewFile([System.IO.Stream]$Source, [string]$Destination) {
  $parent = [System.IO.Path]::GetDirectoryName($Destination)
  if ($parent -and -not [System.IO.Directory]::Exists($parent)) {
    [System.IO.Directory]::CreateDirectory($parent) | Out-Null
  }
  $destinationStream = [System.IO.FileStream]::new(
    $Destination, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::None)
  try {
    $Source.CopyTo($destinationStream)
    $destinationStream.Flush($true)
  } finally {
    $destinationStream.Dispose()
  }
}

function CopyPathToNewFile([string]$Source, [string]$Destination) {
  $sourceStream = [System.IO.FileStream]::new(
    $Source, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::Read)
  try {
    CopyStreamToNewFile $sourceStream $Destination
  } finally {
    $sourceStream.Dispose()
  }
}

function TestFilesEqual([string]$Left, [string]$Right) {
  if (-not [System.IO.File]::Exists($Left) -or -not [System.IO.File]::Exists($Right)) { return $false }
  $leftInfo = [System.IO.FileInfo]::new($Left)
  $rightInfo = [System.IO.FileInfo]::new($Right)
  if ($leftInfo.Length -ne $rightInfo.Length) { return $false }
  return (Get-FileHash -LiteralPath $Left -Algorithm SHA256).Hash -eq
         (Get-FileHash -LiteralPath $Right -Algorithm SHA256).Hash
}

function AssertTargetState(
  [string]$Root, [string]$Relative,
  [AllowNull()][string]$ExpectedIdentity, [AllowNull()][string]$ExpectedSnapshot
) {
  AssertPathChainSafe $Root $Relative
  $target = Join-Path $Root $Relative
  $item = GetExistingItem $target
  if ($null -eq $item) {
    if ($ExpectedIdentity) { throw "Managed target disappeared during update: $target" }
    return
  }
  if ($item.PSIsContainer -or (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
    throw "Managed target changed type or became a reparse point: $target"
  }
  $identity = AssertSingleLinkFile $target
  if (-not $ExpectedIdentity) { throw "Managed target appeared during update: $target" }
  if ($identity -ne $ExpectedIdentity) { throw "Managed target changed during update: $target" }
  if (-not $ExpectedSnapshot -or -not (TestFilesEqual $target $ExpectedSnapshot)) {
    throw "Managed target content changed during update: $target"
  }
}

function InstallFileAtomically(
  [string]$Root, [string]$Relative, [string]$Source,
  [AllowNull()][string]$ExpectedIdentity, [AllowNull()][string]$ExpectedSnapshot,
  [bool]$RequireExpectedState
) {
  $target = Join-Path $Root $Relative
  $parent = [System.IO.Path]::GetDirectoryName($target)
  AssertPathChainSafe $Root $Relative
  AssertPinnedDirectory $parent
  PinSecureDirectory $parent
  $temporary = Join-Path $parent ('.paper-wiki-write-' + [Guid]::NewGuid().ToString('N'))
  try {
    CopyPathToNewFile $Source $temporary
    AssertPathChainSafe $Root $Relative
    AssertPinnedDirectory $parent
    if ($RequireExpectedState) {
      AssertTargetState $Root $Relative $ExpectedIdentity $ExpectedSnapshot
    } else {
      $item = GetExistingItem $target
      if ($null -ne $item) {
        if ($item.PSIsContainer -or (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
          throw "Cannot atomically replace unsafe rollback target: $target"
        }
        [void](AssertSingleLinkFile $target)
      }
    }
    AssertPathChainSafe $Root $Relative
    AssertPinnedDirectory $parent
    $flags = [uint32](1 -bor 8)
    if (-not [PaperWikiNativeMethods]::MoveFileEx($temporary, $target, $flags)) {
      $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
      throw "Atomic replace failed (Win32 $errorCode): $target"
    }
  } finally {
    if ([System.IO.File]::Exists($temporary)) {
      try { [System.IO.File]::Delete($temporary) } catch { }
    }
  }
}

function RemoveFileAtomically(
  [string]$Root, [string]$Relative,
  [AllowNull()][string]$ExpectedIdentity, [AllowNull()][string]$ExpectedSnapshot,
  [bool]$RequireExpectedState
) {
  $target = Join-Path $Root $Relative
  $parent = [System.IO.Path]::GetDirectoryName($target)
  AssertPathChainSafe $Root $Relative
  AssertPinnedDirectory $parent
  if ($RequireExpectedState) {
    AssertTargetState $Root $Relative $ExpectedIdentity $ExpectedSnapshot
  } else {
    $item = GetExistingItem $target
    if ($null -eq $item) { return }
    if ($item.PSIsContainer -or (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
      throw "Cannot atomically remove unsafe rollback target: $target"
    }
    [void](AssertSingleLinkFile $target)
  }
  if (-not [System.IO.File]::Exists($target)) { return }
  AssertPathChainSafe $Root $Relative
  AssertPinnedDirectory $parent
  if (-not [PaperWikiNativeMethods]::DeleteFile($target)) {
    $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    throw "Atomic delete failed (Win32 $errorCode): $target"
  }
}

function PreflightSources([bool]$ForUpdate) {
  EnsureFile (Join-Path $script:SkillRoot 'VERSION')
  foreach ($name in $script:cmds) { EnsureFile (Join-Path $script:SkillRoot "commands\$name.md") }
  foreach ($name in $script:agents) { EnsureFile (Join-Path $script:SkillRoot "agents\$name.md") }
  foreach ($name in $protocolDocs) { EnsureFile (Join-Path $script:SkillRoot "docs\$name") }
  EnsureFile (Join-Path $script:SkillRoot "templates\$($script:Variant)\CLAUDE.md.tmpl")
  EnsureFile (Join-Path $script:SkillRoot 'templates\common\AGENTS.md.tmpl')
  EnsureFile (Join-Path $script:SkillRoot 'templates\common\paper-wiki-project.SKILL.md.tmpl')
  if (-not $ForUpdate) {
    EnsureFile (Join-Path $script:SkillRoot "templates\$($script:Variant)\WIKI.md.tmpl")
    EnsureFile (Join-Path $script:SkillRoot "templates\$($script:Variant)\research.md.tmpl")
    EnsureFile (Join-Path $script:SkillRoot "templates\$($script:Variant)\README.md.tmpl")
    foreach ($name in @('mineru_remote_ocr.py','mineru_local_ocr.py','extract_pptx.py')) {
      EnsureFile (Join-Path $script:SkillRoot "scripts\$name")
    }
  }
}

function CopyManagedClaudeAssets([string]$Root) {
  foreach ($name in $script:cmds) {
    CopyFile (Join-Path $script:SkillRoot "commands\$name.md") (Join-Path $Root ".claude\commands\$name.md")
  }
  foreach ($name in $script:agents) {
    CopyFile (Join-Path $script:SkillRoot "agents\$name.md") (Join-Path $Root ".claude\agents\$name.md")
  }
}

function CopyProtocolDocs([string]$Root) {
  foreach ($name in $protocolDocs) {
    CopyFile (Join-Path $script:SkillRoot "docs\$name") (Join-Path $Root ".paper-wiki\docs\$name")
  }
}

function WriteClientAdapters([string]$Root) {
  Render (Join-Path $script:SkillRoot "templates\$($script:Variant)\CLAUDE.md.tmpl") (Join-Path $Root 'CLAUDE.md')
  Render (Join-Path $script:SkillRoot 'templates\common\AGENTS.md.tmpl') (Join-Path $Root 'AGENTS.md')
  Render (Join-Path $script:SkillRoot 'templates\common\paper-wiki-project.SKILL.md.tmpl') (Join-Path $Root '.agents\skills\paper-wiki-project\SKILL.md')
}

function BuildManagedStage([string]$Root) {
  [System.IO.Directory]::CreateDirectory($Root) | Out-Null
  CopyManagedClaudeAssets $Root
  CopyProtocolDocs $Root
  WriteClientAdapters $Root
  WriteProjectManifest (Join-Path $Root '.paper-wiki\project.yaml')
}

function BuildCreateStage([string]$Root) {
  $dirs = @(
    (Join-Path $Root "raw\$script:Topic"),
    (Join-Path $Root 'scripts'),
    (Join-Path $Root '.claude\commands'),
    (Join-Path $Root '.claude\agents'),
    (Join-Path $Root '.agents\skills\paper-wiki-project'),
    (Join-Path $Root '.paper-wiki\docs'),
    (Join-Path $Root 'wiki\notes')
  )
  if ($script:Variant -eq 'research') {
    $wikiDirs = @('papers','concepts','gaps','experiments')
  } else {
    $wikiDirs = @('lectures','topics','practice')
  }
  foreach ($name in $wikiDirs) { $dirs += (Join-Path $Root "wiki\$name") }
  foreach ($dir in $dirs) { [System.IO.Directory]::CreateDirectory($dir) | Out-Null }
  foreach ($name in $wikiDirs) { WriteUtf8 (Join-Path $Root "wiki\$name\.gitkeep") '' }
  WriteUtf8 (Join-Path $Root "raw\$script:Topic\.gitkeep") ''
  WriteUtf8 (Join-Path $Root 'wiki\notes\.gitkeep') ''

  CopyManagedClaudeAssets $Root
  CopyProtocolDocs $Root
  foreach ($name in @('mineru_remote_ocr.py','mineru_local_ocr.py','extract_pptx.py')) {
    CopyFile (Join-Path $script:SkillRoot "scripts\$name") (Join-Path $Root "scripts\$name")
  }
  foreach ($name in @('mineru_remote_ocr.py','mineru_local_ocr.py')) {
    $target = Join-Path $Root "scripts\$name"
    $content = (ReadUtf8 $target).Replace('__WIKI_NS__', $script:ns).Replace('__WIKI_TOPIC__', $script:Topic)
    WriteUtf8 $target $content
  }
  Render (Join-Path $script:SkillRoot "templates\$($script:Variant)\WIKI.md.tmpl") (Join-Path $Root 'WIKI.md')
  Render (Join-Path $script:SkillRoot "templates\$($script:Variant)\research.md.tmpl") (Join-Path $Root 'research.md')
  Render (Join-Path $script:SkillRoot "templates\$($script:Variant)\README.md.tmpl") (Join-Path $Root 'README.md')
  WriteClientAdapters $Root
  WriteProjectManifest (Join-Path $Root '.paper-wiki\project.yaml')
}

function RemoveDirectorySafe([string]$Path) {
  if (-not [System.IO.Directory]::Exists($Path)) { return }
  $full = [System.IO.Path]::GetFullPath($Path)
  $root = [System.IO.Path]::GetPathRoot($full)
  if ($full -eq $root -or $full.Length -lt ($root.Length + 8)) {
    throw "Refusing unsafe temporary directory removal: $full"
  }
  [System.IO.Directory]::Delete($full, $true)
}

if (-not $SkillRoot) { $SkillRoot = Split-Path -Parent $PSScriptRoot }
$SkillRoot = [System.IO.Path]::GetFullPath($SkillRoot)
$NewPath = [System.IO.Path]::GetFullPath($NewPath)
$date = Get-Date -Format 'yyyy-MM-dd'

if ($Update) {
  $rootItem = GetExistingItem $NewPath
  if ($null -eq $rootItem -or -not $rootItem.PSIsContainer) {
    Fail "Project path does not exist: $NewPath"
  }
  AssertManagedSecurity $NewPath $true

  $manifestPath = Join-Path $NewPath '.paper-wiki\project.yaml'
  $wikiPath = Join-Path $NewPath 'WIKI.md'
  $claudePath = Join-Path $NewPath 'CLAUDE.md'
  $manifestSpec = ReadManifestScalar $manifestPath 'spec'
  $manifestVariant = ReadManifestScalar $manifestPath 'variant'
  $manifestTopic = ReadManifestScalar $manifestPath 'topic'
  $manifestName = ReadManifestScalar $manifestPath 'project_name'
  $wikiText = if ([System.IO.File]::Exists($wikiPath)) { ReadUtf8 $wikiPath } else { $null }
  $oldClaudeText = if ([System.IO.File]::Exists($claudePath)) { ReadUtf8 $claudePath } else { $null }
  $wikiVariant = InferVariantFromText $wikiText
  $claudeVariant = InferVariantFromText $oldClaudeText

  $hasManifestSignature = $manifestSpec -like 'llm-wiki/*'
  $hasWikiSignature = [System.IO.Directory]::Exists((Join-Path $NewPath '.paper-wiki')) -and
                      ($wikiText -match '(?im)paper-wiki-variant\s*:\s*(research|course)')
  $hasLegacyCommands = [System.IO.File]::Exists((Join-Path $NewPath '.claude\commands\wiki-init.md')) -and
                       [System.IO.File]::Exists((Join-Path $NewPath '.claude\commands\wiki-compile.md'))
  $hasLegacySignature = $hasLegacyCommands -and $claudeVariant -and
                        ($oldClaudeText -match '(?i)LLM Wiki|paper-wiki')
  $hasThinClaudeAdapter = $oldClaudeText -and
                          ($oldClaudeText -match '(?im)^# Claude Code project adapter\s*$') -and
                          ($oldClaudeText -match '(?i)WIKI\.md.*only canonical source')
  $hasCodexAssets = [System.IO.File]::Exists((Join-Path $NewPath 'AGENTS.md')) -or
                    [System.IO.Directory]::Exists((Join-Path $NewPath '.agents'))
  $isConfirmedLegacy = $hasLegacySignature -and -not $hasManifestSignature -and
                       -not [System.IO.File]::Exists($wikiPath) -and
                       -not $hasThinClaudeAdapter -and -not $hasCodexAssets
  if (-not ($hasManifestSignature -or $hasWikiSignature -or $hasLegacySignature)) {
    Fail 'This does not look like a paper-wiki project. No paper-wiki manifest, marked WIKI.md, or legacy command/template signature was found.'
  }
  if (-not [System.IO.File]::Exists($wikiPath)) {
    if ($hasManifestSignature) {
      Fail 'Managed paper-wiki project is missing canonical WIKI.md. Restore it from version control or backup; update will not reconstruct it from a client adapter.' 3
    }
    if (-not $isConfirmedLegacy) {
      Fail 'WIKI.md is missing, and this is not a confirmed Claude-only legacy project with a full CLAUDE.md rulebook. Refusing migration.' 3
    }
  }

  $variantEvidence = @()
  if ($manifestVariant -in @('research','course')) {
    $variantEvidence += [pscustomobject]@{ Source = 'project.yaml'; Variant = $manifestVariant }
  }
  if ($wikiVariant) { $variantEvidence += [pscustomobject]@{ Source = 'WIKI.md'; Variant = $wikiVariant } }
  if ($claudeVariant) { $variantEvidence += [pscustomobject]@{ Source = 'CLAUDE.md'; Variant = $claudeVariant } }
  $distinctVariants = @($variantEvidence | Select-Object -ExpandProperty Variant -Unique)
  if ($distinctVariants.Count -gt 1) {
    $details = ($variantEvidence | ForEach-Object { "$($_.Source)=$($_.Variant)" }) -join ', '
    Fail "Variant conflict across project metadata: $details." 3
  }

  $inferredVariant = $null
  $inferredSource = $null
  if ($manifestVariant -in @('research','course')) {
    $inferredVariant = $manifestVariant
    $inferredSource = 'project.yaml'
  } elseif ($wikiVariant) {
    $inferredVariant = $wikiVariant
    $inferredSource = 'WIKI.md'
  } elseif ($claudeVariant) {
    $inferredVariant = $claudeVariant
    $inferredSource = 'CLAUDE.md'
  }
  if ($variantWasExplicit -and $inferredVariant -and $Variant -ne $inferredVariant) {
    Fail "Variant conflict: requested '$Variant' but $inferredSource says '$inferredVariant'." 3
  }
  if ($variantWasExplicit) {
    $Variant = $Variant.ToLowerInvariant()
  } elseif ($inferredVariant) {
    $Variant = $inferredVariant
  } else {
    Fail 'Cannot infer variant. Pass -Variant research or -Variant course.' 2
  }

  $rawTopic = $null
  $rawRoot = Join-Path $NewPath 'raw'
  if ([System.IO.Directory]::Exists($rawRoot)) {
    $rawDirs = @(Get-ChildItem -LiteralPath $rawRoot -Directory -Force |
      Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0 } |
      Sort-Object Name)
    if ($rawDirs.Count -gt 1) {
      $rawNames = ($rawDirs | Select-Object -ExpandProperty Name) -join ', '
      Fail "Topic conflict: multiple raw topic directories were found: $rawNames." 3
    }
    if ($rawDirs.Count -eq 1) { $rawTopic = $rawDirs[0].Name }
  }
  if ($topicWasExplicit) {
    ValidateTopic $Topic
    if ($manifestTopic -and $Topic -ne $manifestTopic) {
      Fail "Topic conflict: requested '$Topic' but project.yaml says '$manifestTopic'." 3
    }
    if ($rawTopic -and $Topic -ne $rawTopic) {
      Fail "Topic conflict: requested '$Topic' but raw directory says '$rawTopic'." 3
    }
  } else {
    if ($manifestTopic -and $rawTopic -and $manifestTopic -ne $rawTopic) {
      Fail "Topic conflict across project metadata: project.yaml=$manifestTopic, raw=$rawTopic." 3
    }
    if ($manifestTopic) { $Topic = $manifestTopic }
    elseif ($rawTopic) { $Topic = $rawTopic }
    else { $Topic = Split-Path -Leaf $NewPath }
  }
  ValidateTopic $Topic
  if (-not $ProjectName) { $ProjectName = $manifestName }
  if (-not $ProjectName) { $ProjectName = Split-Path -Leaf $NewPath }
  $ns = (($Topic -replace '[^a-zA-Z0-9]+','_').Trim('_')).ToLowerInvariant()
  SetVariantAssets $Variant

  EnsureFile (Join-Path $SkillRoot 'VERSION')
  $scaffoldVersion = (ReadUtf8 (Join-Path $SkillRoot 'VERSION')).Trim()
  if (-not $scaffoldVersion) { Fail 'VERSION is empty.' }
  PreflightSources $true

  $migrateWiki = $isConfirmedLegacy
  InitializeSecureUpdateApi
  $projectParent = [System.IO.Path]::GetDirectoryName($NewPath)
  if (-not $projectParent) { Fail "Cannot determine project parent for secure update: $NewPath" 2 }
  $transactionRoot = Join-Path $projectParent ('.paper-wiki-update-' + [Guid]::NewGuid().ToString('N'))
  $stageRoot = Join-Path $transactionRoot 'stage'
  $backupRoot = Join-Path $transactionRoot 'backup'
  $targets = @(GetManagedFileRelatives $migrateWiki)
  $existed = @{}
  $targetIdentity = @{}
  $createdDirs = New-Object System.Collections.Generic.List[string]
  $committedTargets = New-Object System.Collections.Generic.List[string]
  $committedExisted = @{}
  $script:pinnedDirectoryHandles = @{}
  $script:pinnedDirectoryIdentities = @{}
  $commitStarted = $false
  try {
    PinSecureDirectory $NewPath
    [System.IO.Directory]::CreateDirectory($stageRoot) | Out-Null
    [System.IO.Directory]::CreateDirectory($backupRoot) | Out-Null
    BuildManagedStage $stageRoot
    if ($migrateWiki) { CopyFile $claudePath (Join-Path $stageRoot 'WIKI.md') }

    AssertManagedSecurity $NewPath $migrateWiki
    foreach ($relative in $targets) {
      $target = Join-Path $NewPath $relative
      $item = GetExistingItem $target
      if ($null -ne $item) {
        if ($item.PSIsContainer) { throw "Managed file target is a directory: $target" }
        if (($item.Attributes -band [System.IO.FileAttributes]::ReadOnly) -ne 0) {
          throw "Managed file target is read-only: $target"
        }
        $opened = OpenSecureNativeItem $target $false $false
        if ($opened.Information.NumberOfLinks -ne 1) {
          $opened.Handle.Dispose()
          throw "Refusing hard-linked managed file (link count $($opened.Information.NumberOfLinks)): $target"
        }
        $existed[$relative] = $true
        $targetIdentity[$relative] = $opened.Identity
        $sourceStream = [System.IO.FileStream]::new($opened.Handle, [System.IO.FileAccess]::Read)
        try {
          CopyStreamToNewFile $sourceStream (Join-Path $backupRoot $relative)
        } finally {
          $sourceStream.Dispose()
        }
      } else {
        $existed[$relative] = $false
        $targetIdentity[$relative] = $null
      }
    }

    $commitStarted = $true
    foreach ($relative in @('.paper-wiki','.paper-wiki\docs','.claude','.claude\commands','.claude\agents','.agents','.agents\skills','.agents\skills\paper-wiki-project')) {
      $dir = Join-Path $NewPath $relative
      if (-not [System.IO.Directory]::Exists($dir)) {
        AssertPathChainSafe $NewPath $relative
        [System.IO.Directory]::CreateDirectory($dir) | Out-Null
        $createdDirs.Add($dir)
      }
      PinSecureDirectory $dir
    }
    foreach ($relative in $targets) {
      $target = Join-Path $NewPath $relative
      $staged = Join-Path $stageRoot $relative
      $snapshot = if ($existed[$relative]) { Join-Path $backupRoot $relative } else { $null }
      if ([System.IO.File]::Exists($staged)) {
        InstallFileAtomically $NewPath $relative $staged $targetIdentity[$relative] $snapshot $true
        $committedTargets.Add($relative)
        $committedExisted[$relative] = $existed[$relative]
        if (-not (TestFilesEqual $staged $target)) {
          throw "Committed file verification failed: $target"
        }
        [void](AssertSingleLinkFile $target)
      } elseif ($existed[$relative]) {
        RemoveFileAtomically $NewPath $relative $targetIdentity[$relative] $snapshot $true
        $committedTargets.Add($relative)
        $committedExisted[$relative] = $true
        if ($null -ne (GetExistingItem $target)) {
          throw "Committed deletion verification failed: $target"
        }
      }
    }
    $commitStarted = $false
  } catch {
    $failure = $_
    $rollbackErrors = New-Object System.Collections.Generic.List[string]
    if ($commitStarted) {
      for ($index = $committedTargets.Count - 1; $index -ge 0; $index--) {
        $relative = $committedTargets[$index]
        $target = Join-Path $NewPath $relative
        try {
          if ($committedExisted[$relative]) {
            $backup = Join-Path $backupRoot $relative
            InstallFileAtomically $NewPath $relative $backup $null $null $false
            if (-not (TestFilesEqual $backup $target)) {
              throw "Restored file does not match its backup: $target"
            }
            [void](AssertSingleLinkFile $target)
          } else {
            RemoveFileAtomically $NewPath $relative $null $null $false
            if ($null -ne (GetExistingItem $target)) {
              throw "New managed file still exists after rollback: $target"
            }
          }
        } catch {
          $rollbackErrors.Add("$relative -> $($_.Exception.Message)")
        }
      }
      for ($index = $createdDirs.Count - 1; $index -ge 0; $index--) {
        $dir = $createdDirs[$index]
        try {
          UnpinSecureDirectory $dir
          if ([System.IO.Directory]::Exists($dir)) {
            $relativeDir = $dir.Substring($NewPath.TrimEnd('\').Length).TrimStart('\')
            AssertPathChainSafe $NewPath $relativeDir
            if (@(Get-ChildItem -LiteralPath $dir -Force).Count -ne 0) {
              throw "Created directory is not empty: $dir"
            }
            [System.IO.Directory]::Delete($dir, $false)
          }
          if ($null -ne (GetExistingItem $dir)) { throw "Created directory still exists: $dir" }
        } catch {
          $rollbackErrors.Add("$dir -> $($_.Exception.Message)")
        }
      }
    }
    if ($rollbackErrors.Count -gt 0) {
      [Console]::Error.WriteLine("paper-wiki update failed; rollback incomplete: $($failure.Exception.Message)")
      foreach ($rollbackError in $rollbackErrors) { [Console]::Error.WriteLine("  $rollbackError") }
      [Console]::Error.WriteLine("Backup preserved at: $backupRoot")
      exit 4
    }
    if ([System.IO.Directory]::Exists($transactionRoot)) {
      try { RemoveDirectorySafe $transactionRoot } catch { }
    }
    if ($commitStarted) {
      Fail "paper-wiki update failed and rollback was verified: $($failure.Exception.Message)" 1
    }
    Fail "paper-wiki update failed before commit; target was not modified: $($failure.Exception.Message)" 1
  } finally {
    foreach ($handle in @($script:pinnedDirectoryHandles.Values)) {
      try { $handle.Dispose() } catch { }
    }
    $script:pinnedDirectoryHandles.Clear()
    $script:pinnedDirectoryIdentities.Clear()
  }
  if ([System.IO.Directory]::Exists($transactionRoot)) { RemoveDirectorySafe $transactionRoot }
  if ($migrateWiki) { Write-Host 'Migrated legacy CLAUDE.md to canonical WIKI.md.' }
  Write-Host "Updated paper-wiki scaffold $scaffoldVersion ($Variant)."
  Write-Host 'Preserved WIKI.md, research.md and README.md.'
  exit 0
}

if (-not $topicWasExplicit -or -not $Topic) { Fail 'required: -Topic (and -NewPath) when not using -Update' 2 }
ValidateTopic $Topic
if (-not $ProjectName) { $ProjectName = $Topic }
if (-not $variantWasExplicit) { $Variant = 'research' }
else { $Variant = $Variant.ToLowerInvariant() }
$ns = (($Topic -replace '[^a-zA-Z0-9]+','_').Trim('_')).ToLowerInvariant()
SetVariantAssets $Variant

$existingRoot = GetExistingItem $NewPath
if ($null -ne $existingRoot) {
  AssertNotReparse $NewPath
  if (-not $existingRoot.PSIsContainer) { Fail "Project path already exists and is not a directory: $NewPath" }
  if (@(Get-ChildItem -LiteralPath $NewPath -Force | Select-Object -First 1).Count -gt 0) {
    Fail "Refusing to create in an existing non-empty directory: $NewPath"
  }
}

EnsureFile (Join-Path $SkillRoot 'VERSION')
$scaffoldVersion = (ReadUtf8 (Join-Path $SkillRoot 'VERSION')).Trim()
if (-not $scaffoldVersion) { Fail 'VERSION is empty.' }
PreflightSources $false

$parent = [System.IO.Path]::GetDirectoryName($NewPath)
if (-not $parent) { Fail "Cannot determine parent directory for: $NewPath" }
[System.IO.Directory]::CreateDirectory($parent) | Out-Null
$stageRoot = Join-Path $parent (".paper-wiki-bootstrap-" + [Guid]::NewGuid().ToString('N'))
$restoreEmptyRoot = $false
try {
  [System.IO.Directory]::CreateDirectory($stageRoot) | Out-Null
  BuildCreateStage $stageRoot

  $existingRoot = GetExistingItem $NewPath
  if ($null -ne $existingRoot) {
    AssertNotReparse $NewPath
    if (-not $existingRoot.PSIsContainer) { throw "Project path already exists and is not a directory: $NewPath" }
    if (@(Get-ChildItem -LiteralPath $NewPath -Force | Select-Object -First 1).Count -gt 0) {
      throw "Refusing to create in an existing non-empty directory: $NewPath"
    }
    [System.IO.Directory]::Delete($NewPath, $false)
    $restoreEmptyRoot = $true
  }
  [System.IO.Directory]::Move($stageRoot, $NewPath)
  $restoreEmptyRoot = $false
} catch {
  $failure = $_
  if ($restoreEmptyRoot -and -not [System.IO.Directory]::Exists($NewPath)) {
    [System.IO.Directory]::CreateDirectory($NewPath) | Out-Null
  }
  if ([System.IO.Directory]::Exists($stageRoot)) { RemoveDirectorySafe $stageRoot }
  Fail "paper-wiki create failed without modifying the target: $($failure.Exception.Message)"
}

Write-Host "Skill root : $SkillRoot"
Write-Host "New project: $NewPath"
Write-Host "Variant    : $Variant"
Write-Host "OCR ns     : mineru_${ns}_*"
Write-Host "Done -> $NewPath"
Write-Host 'Next: open this project in Claude Code or Codex and ask to initialize the wiki.'
exit 0
