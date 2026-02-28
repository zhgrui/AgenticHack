Here’s a clean Markdown summary you can paste into docs or give to other LLMs 👇

---

# Connecting to a Remote NATS Server and Using Pub/Sub

This guide explains how to connect to a remote **NATS** server using the `nats` CLI and perform basic publish/subscribe operations.

---

## 1. Install NATS CLI

On Linux:

```bash
curl -sf https://binaries.nats.dev/nats-io/natscli/nats@latest | sh
sudo mv nats /usr/local/bin
```

Verify:

```bash
nats --version
```

---

## 2. Resolve IPv4 (If IPv6 Fails)

If you see:

```
dial tcp [IPv6-address]:4222: connect: network is unreachable
```

Your network likely has no IPv6 default route.

Resolve IPv4 manually:

```bash
getent ahostsv4 tinkerlab.vsos.ethz.ch
```

Example output:

```
192.33.91.115
```

Use that IPv4 address when connecting.

---

## 3. Connect to Remote Server

```bash
nats -s nats://192.33.91.115:4222 server ping
```

If authentication is required:

```bash
nats -s nats://USER:PASS@192.33.91.115:4222 server ping
```

---

## 4. Basic Pub/Sub Example

### Terminal 1 — Subscribe

```bash
nats -s nats://192.33.91.115:4222 sub test
```

### Terminal 2 — Publish

```bash
nats -s nats://192.33.91.115:4222 pub test "hello world"
```

Subscriber output:

```
[#1] Received on "test"
hello world
```

---

## 5. Notes About `server info`

If you run:

```bash
nats server info
```

and get:

```
no results received, ensure the account used has system privileges
```

This means:

* You successfully connected
* But your account does not have `$SYS` permissions
* This is normal on shared or production NATS servers

It does **not** mean connection failure.

---

## 6. Minimal Mental Model of NATS Pub/Sub

* A **subject** is a routing key (e.g., `test`)
* Publishers send messages to a subject
* Subscribers receive messages from subjects
* No broker topics need to be pre-created
* Delivery is real-time (unless using JetStream)

---

## 7. Quick Troubleshooting

| Error                     | Meaning                      |
| ------------------------- | ---------------------------- |
| `network is unreachable`  | No IPv6 route (force IPv4)   |
| `connection refused`      | Server not listening on port |
| `i/o timeout`             | Firewall or blocked port     |
| `authorization violation` | Credentials required         |
| `no results received`     | Missing system privileges    |

---

## Minimal Working Command Set

```bash
# Subscribe
nats -s nats://IP:4222 sub demo

# Publish
nats -s nats://IP:4222 pub demo "message"
```

---

This is sufficient for hackathon usage or basic NATS experimentation.
