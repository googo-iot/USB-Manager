# 연결된 이동식 저장장치(USB 메모리, SD 카드, 외장 HDD 등) 정보를 JSON으로 출력한다.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# MSFT_PhysicalDisk 의 BusType 으로 내장 SD 리더(BusType 12/13)까지 구분한다.
# 이 클래스는 환경에 따라 없을 수 있으므로 실패해도 계속 진행한다.
$busByIndex = @{}
try {
    foreach ($pd in Get-CimInstance -Namespace root\Microsoft\Windows\Storage `
                                    -ClassName MSFT_PhysicalDisk -ErrorAction Stop) {
        $idx = $null
        if ([int]::TryParse($pd.DeviceId, [ref]$idx)) { $busByIndex[$idx] = [int]$pd.BusType }
    }
} catch {
    # 무시: BusType 없이 모델명만으로 판별한다.
}

$USB_BUS = 7; $SD_BUS = 12; $MMC_BUS = 13

$devices = @()
foreach ($disk in Get-CimInstance Win32_DiskDrive) {
    $bus = $null
    if ($busByIndex.ContainsKey([int]$disk.Index)) { $bus = $busByIndex[[int]$disk.Index] }

    # 이동식 저장장치만 고른다. 내장 SATA/NVMe 고정 디스크는 제외된다.
    $isRemovable = ($disk.InterfaceType -eq 'USB') `
        -or ($bus -in @($USB_BUS, $SD_BUS, $MMC_BUS)) `
        -or ($disk.MediaType -like '*Removable*')
    if (-not $isRemovable) { continue }

    $volumes = @()
    foreach ($part in Get-CimAssociatedInstance -InputObject $disk -ResultClassName Win32_DiskPartition) {
        foreach ($ld in Get-CimAssociatedInstance -InputObject $part -ResultClassName Win32_LogicalDisk) {
            $volumes += [pscustomobject]@{
                drive_letter  = $ld.DeviceID
                volume_label  = $ld.VolumeName
                file_system   = $ld.FileSystem
                volume_size   = $ld.Size
                free_space    = $ld.FreeSpace
                volume_serial = $ld.VolumeSerialNumber
            }
        }
    }

    $devices += [pscustomobject]@{
        pnp_device_id = $disk.PNPDeviceID
        model         = $disk.Model
        serial_number = $disk.SerialNumber
        size          = $disk.Size
        interface     = $disk.InterfaceType
        media_type    = $disk.MediaType
        bus_type      = $bus
        partitions    = $disk.Partitions
        volumes       = @($volumes)
    }
}

[pscustomobject]@{ devices = @($devices) } | ConvertTo-Json -Depth 6 -Compress
