"""Nginx must re-resolve api_server after recreate, not cache a dead IP.

A static `upstream { server api_server:8080 }` is resolved once at start.
After `compose up --force-recreate --no-deps api_server` the new container
gets a new IP and host /health 502s until someone remembers `nginx -s reload`.
"""

from __future__ import annotations

import unittest
from pathlib import Path

OVERLAY = Path(__file__).resolve().parents[1]
TEMPLATE = OVERLAY / "nginx" / "app.conf.template.api-only"
RUN_NGINX = OVERLAY / "nginx" / "run-nginx.sh"
RECREATE = OVERLAY / "recreate-api-server.sh"


class NginxApiRecreateTest(unittest.TestCase):
    def test_template_uses_docker_dns_variable_proxy(self) -> None:
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("resolver 127.0.0.11", text)
        self.assertIn("valid=2s", text)
        self.assertIn("ipv6=off", text)
        self.assertIn("set $onyx_api_upstream ${ONYX_BACKEND_API_HOST}:8080;", text)
        self.assertIn("proxy_pass http://$onyx_api_upstream;", text)
        self.assertEqual(text.count("proxy_pass http://$onyx_api_upstream;"), 2)
        self.assertNotIn("upstream api_server", text)
        self.assertNotIn("proxy_pass http://api_server;", text)

    def test_envsubst_keeps_nginx_upstream_variable(self) -> None:
        """run-nginx.sh must not envsubst $onyx_api_upstream away."""
        text = RUN_NGINX.read_text(encoding="utf-8")
        self.assertIn("$ONYX_BACKEND_API_HOST", text)
        self.assertNotIn("$onyx_api_upstream", text)

    def test_recreate_script_is_one_compose_command(self) -> None:
        text = RECREATE.read_text(encoding="utf-8")
        self.assertIn("up -d --force-recreate --wait", text)
        self.assertIn("--no-deps api_server", text)
        self.assertIn("docker-compose.releasedesk.yml", text)
        self.assertTrue(RECREATE.exists())


if __name__ == "__main__":
    unittest.main()
