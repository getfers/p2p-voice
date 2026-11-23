"""ICE без trickle и поиск внешних адресов через два STUN-сервера."""

import asyncio
import ipaddress

from aioice.ice import server_reflexive_candidate


STUN_SERVERS = (("stun.l.google.com", 19302), ("stun1.l.google.com", 19302))


def use_stun_servers(pc):
    # aioice 0.13 поддерживает один STUN-адрес. Оба запроса должны идти с
    # тех же сокетов, которые aiortc будет использовать для проверки ICE.
    connection = pc.sctp.transport.transport.iceGatherer._connection
    gather_hosts = connection.get_component_candidates

    async def gather(component, addresses, timeout=5):
        candidates = await gather_hosts(component, addresses, timeout)
        requests = [
            asyncio.create_task(server_reflexive_candidate(protocol, server))
            for protocol in connection._protocols
            if protocol.local_candidate.component == component
            and ipaddress.ip_address(protocol.local_candidate.host).version == 4
            for server in STUN_SERVERS
        ]
        if not requests:
            return candidates
        try:
            done, _ = await asyncio.wait(requests, timeout=timeout)
        finally:
            for request in requests:
                if not request.done():
                    request.cancel()
            await asyncio.gather(*requests, return_exceptions=True)
        seen = set()
        for request in requests:
            if request in done and not request.cancelled() and request.exception() is None:
                candidate, _ = request.result()
                address = (candidate.host, candidate.port, candidate.related_address, candidate.related_port)
                if address not in seen:
                    seen.add(address)
                    candidates.append(candidate)
        return candidates

    connection.get_component_candidates = gather


async def gathered_description(pc, description, stun):
    if stun:
        use_stun_servers(pc)
    await pc.setLocalDescription(description)
    if pc.iceGatheringState != "complete" or pc.localDescription is None:
        raise ValueError("WebRTC не завершил сбор сетевых адресов. Повторите попытку.")
    return pc.localDescription.sdp
