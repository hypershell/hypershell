
#pragma once

#include "hysh/interface/stream_handler.h"

hy_declare_interface(hy_stream_list);
hy_declare_interface(hy_stream_multiplexer);
hy_declare_interface(hy_stream_demultiplexer);
hy_declare_interface(hy_multiplexed_stream_result_listener);
hy_declare_interface(hy_multiplexed_stream_result_writer);
hy_declare_interface(hy_multiplexed_stream_result_channel_factory);

hy_define_interface(hy_stream_list, hy_list)
    hy_error* (*stream_value)(void *self, hy_stream **retval);
    
    hy_error* (*next_stream)(void *self, hy_stream_list **retval);
hy_end

hy_define_interface(hy_stream_multiplexer, hy_object)
    hy_error* (*multiplex_stream)(void *self,
        hy_read_stream *stream, 
        hy_stream_list **retval);
hy_end

hy_define_interface(hy_stream_demultiplexer, hy_object)
    hy_error* (*demultiplex_stream)(void *self, 
        hy_stream_list *streams, 
        hy_read_stream **retval);
hy_end

hy_define_interface(hy_multiplexed_stream_result_listener, hy_result_stream_listener)
   hy_error* (*on_multiplexed_streams_available)(void *self,
       hy_stream_list *streams);
hy_end

hy_define_interface(hy_multiplexed_stream_result_writer, hy_basic_writer)
    hy_error* (*write_multiplexed_streams_result)(void *self,
        hy_stream_list *streams);
hy_end

hy_define_interface(hy_multiplexed_stream_result_channel_factory, hy_object)
     hy_error* (*create_multiplexed_result_stream_channel)(void *self,
        hy_stream_multiplexer *stream_multiplexer,
        hy_multiplexed_stream_result_listener *listener,
        hy_result_stream_writer **retval);
        
    hy_error* (*create_multiplexed_result_stream_writer)(void *self,
        hy_stream_demultiplexer *stream_demultiplexer,
        hy_result_stream_writer *writer,
        hy_multiplexed_stream_result_writer **retval);
hy_end