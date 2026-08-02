from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from devolo_watchdog.config import Settings
from devolo_watchdog.probes import (
    IperfError,
    parse_iperf_mbps,
    patch_devolo_device_interfaces,
    probe_gateway,
    probe_iperf_direction,
    probe_plc_phy,
    probe_wan_iperf,
    run_single_iperf,
)


def make_settings(**kwargs) -> Settings:
    defaults = {
        "iperf_server": "iperf.example.com",
        "iperf_ports": tuple(range(5201, 5206)),
        "remote_probe": "192.168.1.1",
        "devolo_ip": "192.168.1.20",
        "min_upload_mbps": 100.0,
        "min_download_mbps": 80.0,
        "test_bytes": "64M",
        "parallel_streams": 1,
        "iperf_timeout_seconds": 30,
        "iperf_connect_timeout_ms": 3000,
    }
    defaults.update(kwargs)
    return Settings(**defaults)


class ParseIperfTests(unittest.TestCase):
    def test_receiver_rate_is_converted_to_decimal_mbps(self):
        payload = json.dumps({"end": {"sum_received": {"bits_per_second": 123_456_789}}})
        self.assertAlmostEqual(parse_iperf_mbps(payload), 123.456789)

    def test_sum_fallback_rate_is_parsed(self):
        payload = json.dumps({"end": {"sum": {"bits_per_second": 100_000_000}}})
        self.assertEqual(parse_iperf_mbps(payload), 100.0)

    def test_sum_sent_fallback_rate_is_parsed(self):
        payload = json.dumps({"end": {"sum_sent": {"bits_per_second": 80_000_000}}})
        self.assertEqual(parse_iperf_mbps(payload), 80.0)

    def test_invalid_json_raises_iperf_error(self):
        with self.assertRaises(IperfError) as cm:
            parse_iperf_mbps("not json content")
        self.assertIn("Invalid JSON output", str(cm.exception))

    def test_iperf_error_is_raised(self):
        with self.assertRaises(IperfError):
            parse_iperf_mbps(json.dumps({"error": "server is busy"}))

    def test_missing_throughput_field_raises_iperf_error(self):
        with self.assertRaises(IperfError):
            parse_iperf_mbps(json.dumps({"end": {}}))

    def test_non_finite_throughput_raises_iperf_error(self):
        with self.assertRaises(IperfError):
            parse_iperf_mbps(json.dumps({"end": {"sum_received": {"bits_per_second": "NaN"}}}))

    def test_boolean_throughput_is_not_accepted_as_a_number(self):
        with self.assertRaises(IperfError):
            parse_iperf_mbps(json.dumps({"end": {"sum_received": {"bits_per_second": True}}}))


class SingleIperfCommandTests(unittest.TestCase):
    @patch("subprocess.run")
    def test_run_single_iperf_execution(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"end": {"sum_received": {"bits_per_second": 100_000_000}}}),
        )
        val = run_single_iperf(
            server="192.168.1.100",
            port=5201,
            test_bytes="64M",
            parallel=1,
            timeout_seconds=30,
            reverse=True,
        )
        self.assertEqual(val, 100.0)
        cmd = mock_run.call_args[0][0]
        self.assertIn("192.168.1.100", cmd)
        self.assertIn("--reverse", cmd)

    @patch("subprocess.run")
    def test_run_single_iperf_error_extraction(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout=json.dumps({"error": "error - unable to connect to server: Connection refused"}),
            stderr="",
        )
        with self.assertRaises(IperfError) as cm:
            run_single_iperf(
                server="192.168.1.100",
                port=5201,
                test_bytes="64M",
                parallel=1,
                timeout_seconds=30,
                reverse=False,
            )
        self.assertIn("Connection refused", str(cm.exception))

    @patch("subprocess.run")
    def test_run_single_iperf_stderr_error_extraction(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="iperf3: error - parameter error",
        )
        with self.assertRaises(IperfError) as cm:
            run_single_iperf(
                server="192.168.1.100",
                port=5201,
                test_bytes="64M",
                parallel=1,
                timeout_seconds=30,
                reverse=False,
            )
        self.assertIn("parameter error", str(cm.exception))

    @patch("subprocess.run")
    def test_run_single_iperf_missing_binary(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        with self.assertRaises(IperfError) as cm:
            run_single_iperf("iperf.example.com", 5201, "64M", 1, 30, False)
        self.assertIn("iperf3 binary missing", str(cm.exception))

    @patch("subprocess.run")
    def test_run_single_iperf_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="iperf3", timeout=30)
        with self.assertRaises(IperfError) as cm:
            run_single_iperf("iperf.example.com", 5201, "64M", 1, 30, False)
        self.assertIn("exceeded timeout", str(cm.exception))


class RunDirectionTests(unittest.TestCase):
    @patch("devolo_watchdog.probes.run_single_iperf")
    def test_run_direction_error_aggregation(self, mock_single):
        mock_single.side_effect = [
            IperfError("connection refused"),
            IperfError("timeout"),
        ]
        st = make_settings()
        sample, err = probe_iperf_direction(st, (5201, 5202), reverse=False)
        self.assertIsNone(sample)
        self.assertEqual(err, "port 5201: connection refused; port 5202: timeout")


class ProbeWanIperfTests(unittest.TestCase):
    @patch("devolo_watchdog.probes.probe_iperf_direction")
    def test_probe_wan_iperf_full_success(self, mock_direction):
        from devolo_watchdog.models import IperfSample

        mock_direction.side_effect = [
            (IperfSample(150.0, 5201), None),
            (IperfSample(120.0, 5202), None),
        ]
        st = make_settings()
        res = probe_wan_iperf(st, (5201,), (5202,))
        self.assertEqual(res.upload_mbps, 150.0)
        self.assertEqual(res.download_mbps, 120.0)
        self.assertIsNone(res.error)

    @patch("devolo_watchdog.probes.probe_iperf_direction")
    def test_probe_wan_iperf_upload_failure(self, mock_direction):
        mock_direction.return_value = (None, "all ports rejected")
        st = make_settings()
        res = probe_wan_iperf(st, (5201,), (5202,))
        self.assertIsNone(res.upload_mbps)
        self.assertEqual(res.error, "WAN upload test failed: all ports rejected")

    @patch("devolo_watchdog.probes.probe_iperf_direction")
    def test_probe_wan_iperf_missing_error_gets_fallback(self, mock_direction):
        mock_direction.return_value = (None, None)
        st = make_settings()

        res = probe_wan_iperf(st, (5201,), (5202,))

        self.assertEqual(res.error, "WAN upload test failed: unknown error")

    @patch("devolo_watchdog.probes.probe_iperf_direction")
    def test_probe_wan_iperf_download_failure(self, mock_direction):
        from devolo_watchdog.models import IperfSample

        mock_direction.side_effect = [
            (IperfSample(150.0, 5201), None),
            (None, "download port closed"),
        ]
        st = make_settings()
        res = probe_wan_iperf(st, (5201,), (5202,))
        self.assertEqual(res.upload_mbps, 150.0)
        self.assertIsNone(res.download_mbps)
        self.assertEqual(res.error, "WAN download test failed: download port closed")


class ProbeGatewayTests(unittest.TestCase):
    @patch("subprocess.run")
    def test_probe_gateway_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        res = probe_gateway("192.168.1.1")
        self.assertTrue(res.reachable)

    @patch("subprocess.run")
    def test_probe_gateway_fallback_on_exit_code_2(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=2, stderr="ping: invalid option -- 'W'"),
            MagicMock(returncode=0, stderr=""),
        ]
        res = probe_gateway("192.168.1.1")
        self.assertTrue(res.reachable)
        self.assertEqual(mock_run.call_count, 2)

    @patch("subprocess.run")
    def test_probe_gateway_fallback_fails(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=2, stderr="invalid arg"),
            MagicMock(returncode=1, stderr="Destination Host Unreachable"),
        ]
        res = probe_gateway("192.168.1.1")
        self.assertFalse(res.reachable)
        self.assertEqual(res.error, "ping exit code 1: Destination Host Unreachable")

    @patch("subprocess.run")
    def test_probe_gateway_exit_code_1(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="100% packet loss")
        res = probe_gateway("192.168.1.1")
        self.assertFalse(res.reachable)
        self.assertEqual(res.error, "ping exit code 1: 100% packet loss")

    @patch("subprocess.run")
    def test_probe_gateway_missing_binary(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        res = probe_gateway("192.168.1.1")
        self.assertFalse(res.reachable)
        self.assertEqual(res.error, "ping binary missing on system")

    @patch("subprocess.run")
    def test_probe_gateway_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ping", timeout=5)
        res = probe_gateway("192.168.1.1")
        self.assertFalse(res.reachable)
        self.assertEqual(res.error, "ping command timed out after 7s")

    @patch("subprocess.run")
    def test_probe_gateway_generic_exception(self, mock_run):
        mock_run.side_effect = OSError("Permission denied")
        res = probe_gateway("192.168.1.1")
        self.assertFalse(res.reachable)
        self.assertEqual(res.error, "ping execution failed: Permission denied")


class PlcPhyProbeTests(unittest.TestCase):
    def test_async_probe_plc_phy_success(self):
        mock_device_cls = MagicMock()
        mock_device = AsyncMock()
        mock_device_cls.return_value = mock_device
        mock_device.__aenter__.return_value = mock_device
        mock_device.mac = "AA:BB:CC:DD:EE:FF"

        mock_overview = MagicMock()
        rate1 = MagicMock(
            rx_rate=300.0,
            tx_rate=250.0,
            mac_address_from="AA:BB:CC:DD:EE:FF",
            mac_address_to="11:22:33:44:55:66",
        )
        rate2 = MagicMock(
            rx_rate=200.0,
            tx_rate=220.0,
            mac_address_from="11:22:33:44:55:66",
            mac_address_to="aa-bb-cc-dd-ee-ff",
        )
        unrelated_rate = MagicMock(
            rx_rate=5.0,
            tx_rate=5.0,
            mac_address_from="22:22:22:22:22:22",
            mac_address_to="33:33:33:33:33:33",
        )
        mock_overview.data_rates = [rate1, rate2, unrelated_rate]
        mock_device.plcnet.async_get_network_overview.return_value = mock_overview

        res = probe_plc_phy("192.168.1.20", "secret", device_class=mock_device_cls)
        self.assertTrue(res.reachable)
        self.assertEqual(res.rx_rate_mbps, 200.0)
        self.assertEqual(res.tx_rate_mbps, 220.0)
        self.assertEqual(mock_device.password, "secret")

    def test_async_probe_plc_phy_missing_plc_api(self):
        mock_device_cls = MagicMock()
        mock_device = MagicMock()
        mock_device_cls.return_value = mock_device
        mock_device.__aenter__.return_value = mock_device
        mock_device.plcnet = None

        res = probe_plc_phy("192.168.1.20", device_class=mock_device_cls)
        self.assertTrue(res.reachable)
        self.assertEqual(res.error, "PLC API not supported by device")

    def test_async_probe_plc_phy_filters_invalid_rate_values(self):
        mock_device_cls = MagicMock()
        mock_device = AsyncMock()
        mock_device_cls.return_value = mock_device
        mock_device.__aenter__.return_value = mock_device
        mock_device.mac = "AA:BB:CC:DD:EE:FF"

        invalid_rate = MagicMock(
            rx_rate=object(),
            tx_rate="invalid",
            mac_address_from=mock_device.mac,
            mac_address_to="11:22:33:44:55:66",
        )
        valid_rate = MagicMock(
            rx_rate="125.5",
            tx_rate=b"130.5",
            mac_address_from=mock_device.mac,
            mac_address_to="11:22:33:44:55:66",
        )
        overview = MagicMock(data_rates=[invalid_rate, valid_rate])
        mock_device.plcnet.async_get_network_overview.return_value = overview

        res = probe_plc_phy("192.168.1.20", device_class=mock_device_cls)

        self.assertTrue(res.reachable)
        self.assertEqual(res.rx_rate_mbps, 125.5)
        self.assertEqual(res.tx_rate_mbps, 130.5)

    def test_async_probe_plc_phy_device_exception(self):
        mock_device_cls = MagicMock()
        mock_device_cls.side_effect = RuntimeError("Connection timed out")
        res = probe_plc_phy("192.168.1.20", device_class=mock_device_cls)
        self.assertFalse(res.reachable)
        self.assertEqual(res.error, "Connection timed out")


class PatchInterfacesTests(unittest.TestCase):
    @staticmethod
    def test_patch_devolo_device_interfaces():
        async def select_interfaces(_device):
            return ["192.168.1.10"]

        device_class = type(
            "TestDevice",
            (),
            {"_get_relevant_interfaces": select_interfaces},
        )
        patch_devolo_device_interfaces(device_class)
        # Call again to exercise idempotent guard line
        patch_devolo_device_interfaces(device_class)
