
#pragma once

#include "hysh/interface/stream.h"

hy_declare_interface(hy_streamable);

hy_define_interface(hy_streamable, hy_object)
    hy_error (*to_stream)(void *self, hy_read_stream *retval);
hy_end