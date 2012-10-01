
#pragma once

#include "hysh/interface/stream.h"

hy_declare_interface(hy_streamable);
hy_declare_interface(hy_streamable_converter);

hy_define_interface(hy_streamable, hy_object)
    hy_error* (*to_stream)(void *self, hy_read_stream **retval);
hy_end

hy_define_interface(hy_streamable_converter, hy_object)
    hy_error* (*create_streamable_to_stream_proxy)(void *self, 
        hy_streamable *streamable,
        hy_read_stream **retval);
hy_end
