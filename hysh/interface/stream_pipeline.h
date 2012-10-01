
#pragma once

#include "hysh/interface/stream_handler.h"

hy_define_interface(hy_stream_pipeline_node, hy_object)
    hy_error* (*process_stream)(void *self, 
        hy_read_stream *input_stream,
        hy_result_stream_writer *result_writer);
hy_end

hy_define_interface(hy_stream_pipeline_node_factory, hy_object)
    hy_error* (*create_stream_pipeline_node)(void *self,
        hy_table *args, 
        hy_stream_pipeline_node **retval);
hy_end

hy_define_interface(hy_stream_pipeline_node_builder)
    hy_error* (*add_pipeline_node)(void *self,
        hy_stream_pipeline_node *node);
    
    hy_error* (*to_pipeline_node)(void *self,
        hy_stream_pipieline_node **retval);
hy_end