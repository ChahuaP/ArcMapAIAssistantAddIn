$ErrorActionPreference = 'Stop'

function Assert-ExternalBuildPath([string]$Path, [string]$SourceRoot) {
    if (-not [IO.Path]::IsPathFullyQualified($Path)) { throw "Absolute output path required: $Path" }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $source = [IO.Path]::GetFullPath($SourceRoot).TrimEnd('\')
    if ($full -eq [IO.Path]::GetPathRoot($full).TrimEnd('\') -or
        $full.Equals($source, [StringComparison]::OrdinalIgnoreCase) -or
        $full.StartsWith($source + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $source.StartsWith($full + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Output must be outside the source tree and its ancestors: $full"
    }
    $cursor = $full
    while ($cursor) {
        if ([IO.Directory]::Exists($cursor) -and
            ([IO.File]::GetAttributes($cursor) -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Output ancestors must not be junctions or symbolic links: $cursor"
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    return $full
}

function Copy-BuildTree([string]$Source, [string]$Destination, [string[]]$Extra = @()) {
    & robocopy $Source $Destination /E /R:1 /W:1 /NFL /NDL /NJH /NJS @Extra | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copy failed ($LASTEXITCODE): $Source -> $Destination" }
    $global:LASTEXITCODE = 0
}
