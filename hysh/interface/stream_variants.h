
#pragma once

#include "hysh/interface/stream.h"

hy_define_interface(hy_fixed_size_stream, hy_read_stream)
    hy_error* (*stream_size)(void *self, uint64_t *retval);
hy_end

hy_define_interface(hy_sha1sum_stream, hy_fixed_size_stream)
    hy_error* (*sha1sum)(void *self, hy_string **retval);
hy_end

hy_define_interface(hy_fixed_size_stream_factory, hy_object)
    hy_error* (*create_fixed_size_stream)(void *self,
        hy_read_stream *stream,
        uint64_t size,
        hy_fixed_size_stream **retval);
hy_end

hy_define_interface(hy_sha1sum_stream_factory, hy_object)
    hy_error* (*create_sha1sum_stream)(void *self,
        hy_read_stream *stream,
        uint64_t size,
        hy_string *sha1sum,
        hy_sha1sum_stream **retval);
hy_end