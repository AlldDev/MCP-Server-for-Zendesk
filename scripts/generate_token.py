#!/usr/bin/env python3
"""Generate a secure random token for MCP_SERVER_AUTH_TOKEN."""

import secrets

if __name__ == "__main__":
    print(f"Token gerado: {secrets.token_hex(32)}")
