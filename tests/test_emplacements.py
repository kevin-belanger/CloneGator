"""Les emplacements (§3.1), sur une machine simulée.

La station de développement n'a ni NVMe, ni second contrôleur SATA, ni
concentrateur USB : ces cas sont éprouvés ici sur une arborescence /sys
imitée, relevée sur de vraies machines.

    python3 -m unittest -v tests.test_emplacements
"""

from __future__ import annotations

import unittest
from unittest import mock

from clonegator import devices, sysexec

CARTE = "/sys/devices/pci0000:00"

# Deux contrôleurs SATA de deux ports, deux NVMe, un contrôleur USB 3.
LIENS = {
    "/sys/class/ata_port/ata1": f"{CARTE}/0000:00:17.0/ata1/ata_port/ata1",
    "/sys/class/ata_port/ata2": f"{CARTE}/0000:00:17.0/ata2/ata_port/ata2",
    "/sys/class/ata_port/ata3": f"{CARTE}/0000:00:1c.0/0000:05:00.0/ata3/ata_port/ata3",
    "/sys/class/ata_port/ata4": f"{CARTE}/0000:00:1c.0/0000:05:00.0/ata4/ata_port/ata4",
    "/sys/class/nvme/nvme0": f"{CARTE}/0000:00:1d.0/0000:3c:00.0/nvme/nvme0",
    "/sys/class/nvme/nvme1": f"{CARTE}/0000:00:1d.4/0000:3d:00.0/nvme/nvme1",
    "/sys/bus/usb/devices/usb1": f"{CARTE}/0000:00:14.0/usb1",
    "/sys/bus/usb/devices/usb2": f"{CARTE}/0000:00:14.0/usb2",
    # Le connecteur physique n°5 : port 1 côté USB 3, port 5 côté USB 2.
    "/sys/bus/usb/devices/usb2/2-0:1.0/usb2-port1/peer":
        f"{CARTE}/0000:00:14.0/usb1/1-0:1.0/usb1-port5",
    # Les disques.
    "/sys/block/sda": f"{CARTE}/0000:00:1c.0/0000:05:00.0/ata3/host2/target2:0:0/2:0:0:0/block/sda",
    "/sys/block/sdd": f"{CARTE}/0000:00:17.0/ata2/host1/target1:0:0/1:0:0:0/block/sdd",
    "/sys/block/nvme1n1": "/sys/devices/virtual/nvme-subsystem/nvme-subsys1/nvme1n1",
    "/sys/block/nvme0n2": "/sys/devices/virtual/nvme-subsystem/nvme-subsys0/nvme0n2",
    "/sys/block/sdb": f"{CARTE}/0000:00:14.0/usb2/2-1/2-1:1.0/host6/target6:0:0/6:0:0:0/block/sdb",
    "/sys/block/sdc": f"{CARTE}/0000:00:14.0/usb1/1-3/1-3.2/1-3.2:1.0/host7/target7:0:0/7:0:0:0/block/sdc",
    "/sys/block/vda": "/sys/devices/pci0000:00/0000:00:04.0/virtio1/block/vda",
}
REPERTOIRES = {
    "/sys/class/ata_port": ["ata1", "ata2", "ata3", "ata4"],
    "/sys/class/nvme": ["nvme0", "nvme1"],
    "/sys/bus/usb/devices": ["1-0:1.0", "1-3", "2-0:1.0", "2-1", "usb1", "usb2"],
}
ATTRIBUTS = {
    "/sys/class/ata_port/ata1/port_no": "1",
    "/sys/class/ata_port/ata2/port_no": "2",
    "/sys/class/ata_port/ata3/port_no": "1",
    "/sys/class/ata_port/ata4/port_no": "2",
    "/sys/bus/usb/devices/usb1/speed": "480",
    "/sys/bus/usb/devices/usb2/speed": "10000",
}


class MachineSimulee(unittest.TestCase):
    def setUp(self):
        correctifs = [
            mock.patch.object(sysexec, "lister", lambda chemin: sorted(REPERTOIRES.get(chemin, []))),
            mock.patch.object(sysexec, "chemin_reel", lambda chemin: LIENS.get(chemin)),
            mock.patch.object(sysexec, "lire", lambda chemin: ATTRIBUTS.get(chemin)),
        ]
        for correctif in correctifs:
            correctif.start()
            self.addCleanup(correctif.stop)
        self.topologie = devices._Topologie()

    def nom(self, noyau: str) -> str:
        return self.topologie.emplacement(noyau).nom

    def test_sata_numerote_a_la_suite_sur_deux_controleurs(self):
        self.assertEqual([e.nom for e in self.topologie.sata], ["SATA1", "SATA2", "SATA3", "SATA4"])
        self.assertEqual(self.nom("sdd"), "SATA2")
        # Premier port du second contrôleur : SATA3, pas un second « SATA1 ».
        self.assertEqual(self.nom("sda"), "SATA3")
        self.assertEqual(self.topologie.emplacement("sda").cle, "sata:0000:05:00.0:1")

    def test_nvme_par_ordre_des_controleurs(self):
        second = self.topologie.emplacement("nvme1n1")
        self.assertEqual((second.bus, second.nom), (devices.BUS_NVME, "NVMe2"))
        self.assertTrue(second.interne)
        self.assertEqual(self.nom("nvme0n2"), "NVMe1-2")

    def test_usb3_nomme_d_apres_son_jumeau_usb2(self):
        rapide = self.topologie.emplacement("sdb")
        self.assertEqual((rapide.bus, rapide.nom), (devices.BUS_USB, "USB5"))
        self.assertFalse(rapide.interne)

    def test_usb_derriere_un_concentrateur(self):
        self.assertEqual(self.nom("sdc"), "USB3.2")

    def test_autre_bus(self):
        autre = self.topologie.emplacement("vda")
        self.assertEqual((autre.bus, autre.nom), (devices.BUS_AUTRE, "vda"))
        self.assertFalse(devices.admis_en_station(autre))


if __name__ == "__main__":
    unittest.main()
