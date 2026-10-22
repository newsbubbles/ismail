"""YouTube's streamed chat reader (liveChatMessages.streamList): one gRPC call that stays open and hands over each
message as it is written, instead of a list call every few seconds.

The messages are built here from a descriptor of the fields the adapter reads (field numbers from YouTube's
stream_list.proto), so nothing is generated or installed beyond grpcio and protobuf. Each response comes back as the
same dict the REST list call returns, so the adapter handles both readers with one function."""
from google.protobuf import descriptor_pb2, descriptor_pool, json_format, message_factory

TARGET = 'youtube.googleapis.com:443'
METHOD = '/youtube.api.v3.V3DataLiveChatMessageService/StreamList'
PKG = 'youtube.api.v3'

# snippet.type, as the REST API spells it
TYPES = {1: 'textMessageEvent', 2: 'tombstone', 3: 'fanFundingEvent', 4: 'chatEndedEvent',
         5: 'sponsorOnlyModeStartedEvent', 6: 'sponsorOnlyModeEndedEvent', 7: 'newSponsorEvent',
         10: 'userBannedEvent', 15: 'superChatEvent', 16: 'superStickerEvent', 17: 'memberMilestoneChatEvent',
         18: 'membershipGiftingEvent', 19: 'giftMembershipReceivedEvent', 20: 'pollEvent', 21: 'giftEvent'}

S, B, I, U, M = 'string', 'bool', 'int32', 'uint32', 'message'
# message -> [(field, number, type, message type or None, repeated)]; the subset the adapter reads
FIELDS = {
    'LiveChatMessageListRequest': [('live_chat_id', 1, S), ('hl', 2, S), ('profile_image_size', 3, U),
                                   ('page_token', 99, S), ('part', 100, S, None, True)],
    'LiveChatMessageListResponse': [('offline_at', 2, S), ('next_page_token', 100602, S),
                                    ('items', 1007, M, 'LiveChatMessage', True)],
    'LiveChatMessage': [('id', 101, S), ('snippet', 2, M, 'LiveChatMessageSnippet'),
                        ('author_details', 3, M, 'LiveChatMessageAuthorDetails')],
    'LiveChatMessageAuthorDetails': [('channel_id', 10101, S), ('channel_url', 102, S), ('display_name', 103, S),
                                     ('is_verified', 4, B), ('is_chat_owner', 5, B), ('is_chat_sponsor', 6, B),
                                     ('is_chat_moderator', 7, B)],
    'LiveChatMessageSnippet': [('type', 1, I), ('author_channel_id', 301, S), ('published_at', 4, S),
                               ('display_message', 16, S),
                               ('text_message_details', 19, M, 'LiveChatTextMessageDetails'),
                               ('super_chat_details', 27, M, 'LiveChatSuperChatDetails'),
                               ('super_sticker_details', 28, M, 'LiveChatSuperStickerDetails'),
                               ('member_milestone_chat_details', 30, M, 'LiveChatMemberMilestoneChatDetails')],
    'LiveChatTextMessageDetails': [('message_text', 1, S)],
    'LiveChatSuperChatDetails': [('currency', 2, S), ('amount_display_string', 3, S), ('user_comment', 4, S),
                                 ('tier', 5, U)],
    'LiveChatSuperStickerDetails': [('currency', 2, S), ('amount_display_string', 3, S), ('tier', 4, U)],
    'LiveChatMemberMilestoneChatDetails': [('member_level_name', 1, S), ('member_month', 2, U),
                                           ('user_comment', 3, S)],
}
_T = descriptor_pb2.FieldDescriptorProto


def _classes():
    f = descriptor_pb2.FileDescriptorProto(name='ismail_stream_list.proto', package=PKG, syntax='proto2')
    for name, fields in FIELDS.items():
        m = f.message_type.add(name=name)
        for spec in fields:
            fname, num, kind = spec[:3]
            ref, rep = (spec[3] if len(spec) > 3 else None), (len(spec) > 4 and spec[4])
            d = m.field.add(name=fname, number=num, label=_T.LABEL_REPEATED if rep else _T.LABEL_OPTIONAL,
                            type={S: _T.TYPE_STRING, B: _T.TYPE_BOOL, I: _T.TYPE_INT32, U: _T.TYPE_UINT32,
                                  M: _T.TYPE_MESSAGE}[kind])
            if ref:
                d.type_name = f'.{PKG}.{ref}'
    pool = descriptor_pool.DescriptorPool()
    pool.Add(f)
    return {n: message_factory.GetMessageClass(pool.FindMessageTypeByName(f'{PKG}.{n}')) for n in FIELDS}


C = _classes()
Request, Response = C['LiveChatMessageListRequest'], C['LiveChatMessageListResponse']


def to_dict(resp):
    """A streamList response as the REST list call's JSON: items with snippet.type spelled out."""
    d = json_format.MessageToDict(resp)
    for it in d.get('items') or []:
        s = it.get('snippet') or {}
        if isinstance(s.get('type'), int):
            s['type'] = TYPES.get(s['type'], str(s['type']))
    return d


def open_stream(chat_id, page_token, token, timeout, target=TARGET, secure=True):
    """The open call: an iterator of response dicts, and the call itself (cancel() ends it). `token` is an OAuth
    access token (an API key also works, but reads only public chats); `timeout` ends the call after that many
    seconds, so the caller reopens with a fresh token."""
    import grpc
    ch = grpc.secure_channel(target, grpc.ssl_channel_credentials()) if secure else grpc.insecure_channel(target)
    req = Request(live_chat_id=chat_id, part=['id', 'snippet', 'authorDetails'])
    if page_token:
        req.page_token = page_token
    call = ch.unary_stream(METHOD, request_serializer=Request.SerializeToString,
                           response_deserializer=Response.FromString)(
        req, metadata=[('authorization', f'Bearer {token}')], timeout=timeout)

    def items():
        try:
            for r in call:
                yield to_dict(r)
        finally:
            ch.close()
    return items(), call
