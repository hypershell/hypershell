
#pragma once

#include "hysh/interface/fixed_size_stream.h"

hy_define_interface(hy_file_stream_callback, hy_basic_callback)
    hy_error* (*on_file_stream_available)(void *self,
        hy_fixed_size_stream file_stream);
hy_end

hy_define_interface(hy_file_stream_factory, hy_object)
    hy_error* (*create_stream_from_file)(void *self, 
        hy_string file_path,
        hy_file_stream_factory_callback callback);
hy_end