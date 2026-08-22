param(
    [ValidateSet("get", "set", "change", "mute", "unmute")]
    [string]$Action = "get",

    [double]$Value = 0
)

Add-Type @"
using System;
using System.Runtime.InteropServices;

namespace NexoAudio
{
    public enum EDataFlow
    {
        eRender,
        eCapture,
        eAll
    }

    public enum ERole
    {
        eConsole,
        eMultimedia,
        eCommunications
    }

    [ComImport]
    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceEnumerator
    {
        int EnumAudioEndpoints(EDataFlow dataFlow, uint dwStateMask, out IntPtr ppDevices);

        int GetDefaultAudioEndpoint(
            EDataFlow dataFlow,
            ERole role,
            out IMMDevice ppDevice
        );

        int GetDevice(
            [MarshalAs(UnmanagedType.LPWStr)] string pwstrId,
            out IMMDevice ppDevice
        );

        int RegisterEndpointNotificationCallback(IntPtr pClient);
        int UnregisterEndpointNotificationCallback(IntPtr pClient);
    }

    [ComImport]
    [Guid("D666063F-1587-4E43-81F1-B948E807363F")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDevice
    {
        int Activate(
            ref Guid iid,
            uint dwClsCtx,
            IntPtr pActivationParams,
            [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface
        );

        int OpenPropertyStore(int stgmAccess, out IntPtr ppProperties);

        int GetId(
            [MarshalAs(UnmanagedType.LPWStr)] out string ppstrId
        );

        int GetState(out uint pdwState);
    }

    [ComImport]
    [Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    class MMDeviceEnumeratorComObject
    {
    }

    [ComImport]
    [Guid("5CDF2C82-841E-4546-9722-0CF74078229A")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioEndpointVolume
    {
        int RegisterControlChangeNotify(IntPtr pNotify);
        int UnregisterControlChangeNotify(IntPtr pNotify);

        int GetChannelCount(out uint channelCount);

        int SetMasterVolumeLevel(
            float levelDB,
            Guid eventContext
        );

        int SetMasterVolumeLevelScalar(
            float level,
            Guid eventContext
        );

        int GetMasterVolumeLevel(out float levelDB);

        int GetMasterVolumeLevelScalar(
            out float level
        );

        int SetChannelVolumeLevel(
            uint channelNumber,
            float levelDB,
            Guid eventContext
        );

        int SetChannelVolumeLevelScalar(
            uint channelNumber,
            float level,
            Guid eventContext
        );

        int GetChannelVolumeLevel(
            uint channelNumber,
            out float levelDB
        );

        int GetChannelVolumeLevelScalar(
            uint channelNumber,
            out float level
        );

        int SetMute(
            [MarshalAs(UnmanagedType.Bool)] bool isMuted,
            Guid eventContext
        );

        int GetMute(
            [MarshalAs(UnmanagedType.Bool)] out bool isMuted
        );

        int GetVolumeStepInfo(
            out uint step,
            out uint stepCount
        );

        int VolumeStepUp(Guid eventContext);
        int VolumeStepDown(Guid eventContext);

        int QueryHardwareSupport(
            out uint hardwareSupportMask
        );

        int GetVolumeRange(
            out float volumeMinDB,
            out float volumeMaxDB,
            out float volumeIncrementDB
        );
    }

    public static class Audio
    {
        static IAudioEndpointVolume GetEndpoint()
        {
            IMMDeviceEnumerator enumerator =
                (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();

            IMMDevice device;

            Marshal.ThrowExceptionForHR(
                enumerator.GetDefaultAudioEndpoint(
                    EDataFlow.eRender,
                    ERole.eMultimedia,
                    out device
                )
            );

            object endpointObject;

            Guid iid = typeof(IAudioEndpointVolume).GUID;

            Marshal.ThrowExceptionForHR(
                device.Activate(
                    ref iid,
                    23,
                    IntPtr.Zero,
                    out endpointObject
                )
            );

            return (IAudioEndpointVolume)endpointObject;
        }

        public static float GetVolume()
        {
            float level;
            Marshal.ThrowExceptionForHR(
                GetEndpoint().GetMasterVolumeLevelScalar(
                    out level
                )
            );

            return level * 100.0f;
        }

        public static void SetVolume(float level)
        {
            if (level < 0) level = 0;
            if (level > 100) level = 100;

            Marshal.ThrowExceptionForHR(
                GetEndpoint().SetMasterVolumeLevelScalar(
                    level / 100.0f,
                    Guid.Empty
                )
            );
        }

        public static bool GetMute()
        {
            bool muted;

            Marshal.ThrowExceptionForHR(
                GetEndpoint().GetMute(out muted)
            );

            return muted;
        }

        public static void SetMute(bool muted)
        {
            Marshal.ThrowExceptionForHR(
                GetEndpoint().SetMute(
                    muted,
                    Guid.Empty
                )
            );
        }
    }
}
"@

$before = [NexoAudio.Audio]::GetVolume()

switch ($Action) {

    "set" {
        [NexoAudio.Audio]::SetVolume($Value)
    }

    "change" {
        $target = $before + $Value

        if ($target -lt 0) {
            $target = 0
        }

        if ($target -gt 100) {
            $target = 100
        }

        [NexoAudio.Audio]::SetVolume($target)
    }

    "mute" {
        [NexoAudio.Audio]::SetMute($true)
    }

    "unmute" {
        [NexoAudio.Audio]::SetMute($false)
    }
}

$result = [ordered]@{
    ok = $true
    action = $Action
    before_percent = [math]::Round($before, 1)
    volume_percent = [math]::Round(
        [NexoAudio.Audio]::GetVolume(),
        1
    )
    muted = [NexoAudio.Audio]::GetMute()
}

$result | ConvertTo-Json -Compress
