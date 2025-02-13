import requests
import json
import base64
import asyncio
import hashlib
import os
from functools import lru_cache
import aiohttp
from gtts import gTTS

class AivisSpeechClient:
    def __init__(self, endpoints=None):
        self.endpoints = endpoints or [
            "http://localhost:10101",
        ]
        self.current_endpoint_index = 0
        self.session = None
        self.error_counts = {endpoint: 0 for endpoint in self.endpoints}
        self.last_used = None

    async def get_healthy_endpoint(self):
        # ラウンドロビン方式でエンドポイントを試行
        tried_endpoints = set()
        
        while len(tried_endpoints) < len(self.endpoints):
            # 次のエンドポイントを取得
            self.current_endpoint_index = (self.current_endpoint_index + 1) % len(self.endpoints)
            endpoint = self.endpoints[self.current_endpoint_index]
            
            if endpoint in tried_endpoints:
                continue
                
            tried_endpoints.add(endpoint)
            
            try:
                async with self.session.get(f"{endpoint}/version", timeout=2) as response:
                    if response.status == 200:
                        if self.last_used != endpoint:
                            print(f"Switching to endpoint: {endpoint}")
                            self.last_used = endpoint
                        self.error_counts[endpoint] = 0
                        return endpoint
            except Exception as e:
                self.error_counts[endpoint] += 1
                print(f"Error with endpoint {endpoint}: {e} (errors: {self.error_counts[endpoint]})")
                continue

        # すべてのエンドポイントが失敗した場合
        print("All endpoints failed, trying any available endpoint")
        return self.endpoints[self.current_endpoint_index]

    async def synthesize_with_fallback(self, text, speaker_id, output_file):
        try:
            endpoint = await self.get_healthy_endpoint()
            if not endpoint:
                raise Exception("利用可能なエンドポイントがありません")

            print(f"Using endpoint: {endpoint}")

            audio_query = await self._async_request(
                "post", 
                "/audio_query",
                base_url=endpoint,
                params={"text": text, "speaker": speaker_id}
            )
            
            if audio_query:
                audio_data = await self._async_request(
                    "post", 
                    "/synthesis",
                    base_url=endpoint,
                    params={"speaker": speaker_id},
                    json_data=audio_query,
                    headers={"Content-Type": "application/json"}
                )
                
                if audio_data:
                    with open(output_file, 'wb') as f:
                        f.write(audio_data)
                    return True

            # Fallback to gTTS
            print("Falling back to gTTS")
            tts = gTTS(text=text, lang='ja')
            tts.save(output_file)
            return True

        except Exception as e:
            print(f"音声合成エラー: {e}")
            return False

    async def _async_request(self, method, path, base_url=None, params=None, json_data=None, headers=None):
        if base_url is None:
            base_url = self.endpoints[self.current_endpoint_index]
        url = f"{base_url}{path}"
        
        if headers is None:
            headers = {}
            
        try:
            async with self.session.request(method, url, params=params, json=json_data, headers=headers) as response:
                if response.status == 200:
                    if 'application/json' in response.headers.get('Content-Type', ''):
                        return await response.json()
                    return await response.read()
                raise Exception(f"API request failed with status {response.status}")
        except Exception as e:
            print(f"AIVIS API Error: {e}")
            return None
        
    async def init_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self
        
    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None
          
    @lru_cache(maxsize=1000)
    def check_cache(self, text, speaker_id):
        """Check if audio is cached"""
        cache_path = self.get_cache_path(text, speaker_id)
        return os.path.exists(cache_path)

async def synthesize_text_to_file(client, text, speaker_id, output_filename):
    return await client.synthesize_with_fallback(text, speaker_id, output_filename)